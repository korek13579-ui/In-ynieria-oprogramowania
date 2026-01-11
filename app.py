import json
import calendar
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sekretny_klucz_v18_fixing_breaks_and_next_slot'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- MODELE BAZY DANYCH ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(50), nullable=False) 
    salon_id = db.Column(db.Integer, db.ForeignKey('salon.id'), nullable=True)
    work_days = db.Column(db.String(20), default="0,1,2,3,4")
    breaks_json = db.Column(db.String(1000), default="{}")
    reviews_received = db.relationship('Review', foreign_keys='Review.employee_id', backref='employee_reviewed', lazy=True)

class Salon(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(200), nullable=False)
    open_from = db.Column(db.String(5), nullable=False, default="09:00")
    open_to = db.Column(db.String(5), nullable=False, default="17:00")
    margin_type = db.Column(db.String(10), default='percent') 
    margin_value = db.Column(db.Float, default=0.0)
    services = db.relationship('Service', backref='salon', cascade="all, delete-orphan", lazy=True)
    users = db.relationship('User', backref='salon', lazy=True)
    appointments = db.relationship('Appointment', backref='salon_ref', cascade="all, delete-orphan", lazy=True)

class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    salon_id = db.Column(db.Integer, db.ForeignKey('salon.id'), nullable=False)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.String(20), default=lambda: datetime.now().strftime("%Y-%m-%d"))
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), unique=True, nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(50), nullable=False)
    time = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default="oczekuje")
    proposed_date = db.Column(db.String(50), nullable=True)
    proposed_time = db.Column(db.String(50), nullable=True)
    client_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    employee_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    service_id = db.Column(db.Integer, db.ForeignKey('service.id'))
    salon_id = db.Column(db.Integer, db.ForeignKey('salon.id'))
    service = db.relationship('Service')
    employee = db.relationship('User', foreign_keys=[employee_id])
    client = db.relationship('User', foreign_keys=[client_id])
    review_obj = db.relationship('Review', backref='appointment', uselist=False, cascade="all, delete-orphan")

class WorkSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    is_working = db.Column(db.Boolean, default=True)
    start_time = db.Column(db.String(5), nullable=True)
    end_time = db.Column(db.String(5), nullable=True)
    break_start = db.Column(db.String(5), nullable=True)
    break_end = db.Column(db.String(5), nullable=True)
    __table_args__ = (db.UniqueConstraint('employee_id', 'date', name='_employee_date_uc'),)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.template_filter('calc_end_time')
def calc_end_time_filter(start_time_str, duration_minutes):
    try:
        start_dt = datetime.strptime(start_time_str, "%H:%M")
        end_dt = start_dt + timedelta(minutes=int(duration_minutes))
        return end_dt.strftime("%H:%M")
    except:
        return "??"

# --- FUNKCJA Z BLOKADĄ GODZIN Z PRZESZŁOŚCI ---
def get_slots_for_day(date_str, salon, service, employee):
    schedule = WorkSchedule.query.filter_by(employee_id=employee.id, date=date_str).first()
    
    emp_start, emp_end = None, None
    break_start, break_end = None, None
    is_working_day = False
    
    dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = str(dt_obj.weekday())

    # 1. Priorytet: Grafik Dzienny Pracownika
    if schedule:
        if not schedule.is_working: return []
        is_working_day = True
        emp_start = datetime.strptime(schedule.start_time, "%H:%M")
        emp_end = datetime.strptime(schedule.end_time, "%H:%M")
        if schedule.break_start and schedule.break_end:
            break_start = datetime.strptime(schedule.break_start, "%H:%M")
            break_end = datetime.strptime(schedule.break_end, "%H:%M")
    
    # 2. Domyślnie: Godziny Salonu
    else:
        work_days = employee.work_days.split(',') if employee.work_days else []
        if weekday in work_days:
            is_working_day = True
            emp_start = datetime.strptime(salon.open_from, "%H:%M")
            emp_end = datetime.strptime(salon.open_to, "%H:%M")
            if employee.breaks_json:
                try:
                    breaks = json.loads(employee.breaks_json)
                    day_break = breaks.get(weekday)
                    if day_break and day_break.get('start') and day_break.get('end'):
                        break_start = datetime.strptime(day_break['start'], "%H:%M")
                        break_end = datetime.strptime(day_break['end'], "%H:%M")
                except: pass
        else: return []

    if not is_working_day: return []
    if emp_start >= emp_end: return [] # Zabezpieczenie

    existing = Appointment.query.filter(Appointment.employee_id == employee.id, Appointment.date == date_str, Appointment.status != 'odrzucona').all()
    slots = []
    curr = emp_start
    dur = timedelta(minutes=service.duration)
    
    now = datetime.now()
    is_today = (date_str == now.strftime("%Y-%m-%d"))

    while curr + dur <= emp_end:
        free = True
        ns, ne = curr, curr + dur
        
        # 1. Sprawdź czy godzina nie minęła (tylko jeśli to dzisiaj)
        if is_today and ns.time() <= now.time():
            free = False

        # 2. Sprawdź przerwę
        if free and break_start and break_end:
            if max(ns, break_start) < min(ne, break_end): free = False
        
        # 3. Sprawdź inne wizyty
        if free:
            for ex in existing:
                if ex.service:
                    es = datetime.strptime(ex.time, "%H:%M")
                    ee = es + timedelta(minutes=ex.service.duration)
                    if ns < ee and ne > es:
                        free = False; break
        
        if free: slots.append(curr.strftime("%H:%M"))
        curr += timedelta(minutes=5)
    
    return slots

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            login_user(user)
            if user.role == 'admin': return redirect(url_for('admin_panel'))
            if user.role == 'szef': return redirect(url_for('manager_panel'))
            if user.role in ['pracownik', 'szef']: return redirect(url_for('employee_panel'))
            return redirect(url_for('client_dashboard'))
        flash('Bledne dane!')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u = request.form.get('username'); p = request.form.get('password')
        if User.query.filter_by(username=u).first(): flash('Taki uzytkownik juz istnieje!')
        else: db.session.add(User(username=u, password=p, role='klient')); db.session.commit(); flash('Konto zalozone!'); return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('index'))

@app.route('/client')
@login_required
def client_dashboard():
    if current_user.role != 'klient': return redirect(url_for('index'))
    return render_template('client_dashboard.html', appointments=Appointment.query.filter_by(client_id=current_user.id).order_by(Appointment.date, Appointment.time).all())

@app.route('/client/cancel/<int:id>')
@login_required
def client_cancel_appointment(id):
    a = db.session.get(Appointment, id)
    if a and a.client_id == current_user.id: db.session.delete(a); db.session.commit(); flash('Wizyta anulowana.')
    return redirect(url_for('client_dashboard'))

@app.route('/client/respond/<int:id>/<action>')
@login_required
def client_respond_proposal(id, action):
    a = db.session.get(Appointment, id)
    if a and a.client_id == current_user.id and a.status == 'zmiana_terminu':
        if action == 'accept': a.date=a.proposed_date; a.time=a.proposed_time; a.proposed_date=None; a.proposed_time=None; a.status='potwierdzona'; flash('Zaakceptowano!')
        elif action == 'reject': a.status='odrzucona'; flash('Odrzucono.')
        db.session.commit()
    return redirect(url_for('client_dashboard'))

@app.route('/client/review/<int:app_id>', methods=['GET', 'POST'])
@login_required
def client_review(app_id):
    a = db.session.get(Appointment, app_id)
    if not a or a.client_id != current_user.id or a.status != 'zrealizowana' or a.review_obj: return redirect(url_for('client_dashboard'))
    if request.method == 'POST': db.session.add(Review(rating=int(request.form.get('rating')), comment=request.form.get('comment'), appointment_id=a.id, client_id=current_user.id, employee_id=a.employee_id)); db.session.commit(); flash('Dziekujemy!'); return redirect(url_for('client_dashboard'))
    return render_template('review.html', appointment=a)

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin_panel():
    if current_user.role != 'admin': return redirect(url_for('index'))
    if request.method == 'POST':
        if 'add_salon' in request.form: db.session.add(Salon(name=request.form.get('name'), address=request.form.get('address'), open_from=request.form.get('open_from'), open_to=request.form.get('open_to'))); db.session.commit()
        elif 'add_manager' in request.form: db.session.add(User(username=request.form.get('username'), password=request.form.get('password'), role='szef', salon_id=request.form.get('salon_id'))); db.session.commit()
        elif 'add_service_global' in request.form: db.session.add(Service(name=request.form.get('name'), price=float(request.form.get('price')), duration=int(request.form.get('duration')), salon_id=request.form.get('salon_id'))); db.session.commit()
    return render_template('admin.html', salons=Salon.query.all(), users=User.query.all(), services=Service.query.all())

@app.route('/delete/salon/<int:id>')
@login_required
def delete_salon(id):
    if current_user.role == 'admin': db.session.delete(db.session.get(Salon, id)); db.session.commit()
    return redirect(url_for('admin_panel'))
@app.route('/delete/user/<int:id>')
@login_required
def delete_user(id):
    u = db.session.get(User, id)
    if current_user.role == 'admin' and u.username != 'admin': db.session.delete(u); db.session.commit()
    return redirect(url_for('admin_panel'))
@app.route('/delete/service/<int:id>')
@login_required
def delete_service(id):
    if current_user.role == 'admin': db.session.delete(db.session.get(Service, id)); db.session.commit()
    return redirect(url_for('admin_panel'))

@app.route('/manager', methods=['GET', 'POST'])
@login_required
def manager_panel():
    if current_user.role != 'szef': return redirect(url_for('index'))
    my_salon = db.session.get(Salon, current_user.salon_id)
    if request.method == 'POST':
        if 'add_service' in request.form: db.session.add(Service(name=request.form.get('name'), price=float(request.form.get('price')), duration=int(request.form.get('duration')), salon_id=my_salon.id)); db.session.commit()
        elif 'add_employee' in request.form: db.session.add(User(username=request.form.get('username'), password=request.form.get('password'), role='pracownik', salon_id=my_salon.id)); db.session.commit()
        elif 'update_hours' in request.form: my_salon.open_from = request.form.get('open_from'); my_salon.open_to = request.form.get('open_to'); db.session.commit()
        elif 'update_margin' in request.form: my_salon.margin_type = request.form.get('margin_type'); my_salon.margin_value = float(request.form.get('margin_value')); db.session.commit()
    
    apps = Appointment.query.filter_by(salon_id=my_salon.id, status='zrealizowana').all()
    net = sum((a.service.price * (my_salon.margin_value/100.0) if my_salon.margin_type == 'percent' else my_salon.margin_value) for a in apps if a.service)
    staff_report = []
    for m in User.query.filter(User.salon_id == my_salon.id, User.role.in_(['pracownik', 'szef'])).all():
        ma = Appointment.query.filter_by(employee_id=m.id, status='zrealizowana').all()
        revs = Review.query.filter_by(employee_id=m.id).all()
        m_net = 0.0
        for x in ma:
             if x.service:
                 cut = x.service.price * (my_salon.margin_value/100.0) if my_salon.margin_type == 'percent' else my_salon.margin_value
                 m_net += max(0, x.service.price - cut)
        upcoming = Appointment.query.filter(Appointment.employee_id == m.id, Appointment.status == 'potwierdzona', Appointment.date >= datetime.now().strftime("%Y-%m-%d")).order_by(Appointment.date).all()
        staff_report.append({'username': m.username, 'role': m.role, 'gross': sum(x.service.price for x in ma if x.service), 'net': round(m_net,2), 'upcoming': upcoming, 'avg_rating': round(sum(r.rating for r in revs)/len(revs) if revs else 0, 1), 'reviews_count': len(revs)})
    
    revs = db.session.query(Review).join(User, Review.employee_id == User.id).filter(User.salon_id == my_salon.id).all()
    return render_template('manager.html', salon=my_salon, employees=User.query.filter_by(salon_id=my_salon.id, role='pracownik').all(), services=Service.query.filter_by(salon_id=my_salon.id).all(), salon_net_profit=round(net,2), staff_report=staff_report, total_reviews_count=len(revs), salon_avg_rating=round(sum(r.rating for r in revs)/len(revs) if revs else 0, 1))

@app.route('/manager/delete/service/<int:id>')
@login_required
def manager_delete_service(id):
    s = db.session.get(Service, id)
    if s and s.salon_id == current_user.salon_id and current_user.role == 'szef': db.session.delete(s); db.session.commit()
    return redirect(url_for('manager_panel'))
@app.route('/manager/delete/employee/<int:id>')
@login_required
def manager_delete_employee(id):
    e = db.session.get(User, id)
    if e and e.salon_id == current_user.salon_id and current_user.role == 'szef': db.session.delete(e); db.session.commit()
    return redirect(url_for('manager_panel'))

# --- PRACOWNIK ---
@app.route('/employee', methods=['GET', 'POST'])
@login_required
def employee_panel():
    if current_user.role not in ['pracownik', 'szef']: return redirect(url_for('index'))
    salon = db.session.get(Salon, current_user.salon_id)
    now = datetime.now()
    try: year, month = int(request.args.get('year', now.year)), int(request.args.get('month', now.month))
    except: year, month = now.year, now.month

    if request.method == 'POST':
        if 'update_day_schedule' in request.form:
            d = request.form.get('date_to_edit'); iw = (request.form.get('is_working') == 'on')
            s = WorkSchedule.query.filter_by(employee_id=current_user.id, date=d).first()
            if not s: s = WorkSchedule(employee_id=current_user.id, date=d); db.session.add(s)
            s.is_working = iw
            if iw: s.start_time = request.form.get('start_time'); s.end_time = request.form.get('end_time'); s.break_start = request.form.get('break_start') or None; s.break_end = request.form.get('break_end') or None
            db.session.commit(); flash(f'Zaktualizowano {d}!'); return redirect(url_for('employee_panel', year=year, month=month))
        elif 'action' in request.form and request.form.get('action') == 'propose_change':
             a = db.session.get(Appointment, request.form.get('appointment_id'))
             nd, nt = request.form.get('new_date'), request.form.get('new_time')
             if a: 
                 a.proposed_date, a.proposed_time, a.status = nd, nt, 'zmiana_terminu'; db.session.commit(); flash('Wyslano propozycje.')
        elif 'appointment_id' in request.form:
             a = db.session.get(Appointment, request.form.get('appointment_id')); act = request.form.get('action')
             if a:
                 if act == 'confirm': a.status='potwierdzona'
                 elif act in ['reject', 'cancel_confirmed']: a.status='odrzucona'
                 elif act == 'complete': a.status='zrealizowana'
                 db.session.commit(); flash(f'Status: {a.status}')
    
    # Dane kalendarza
    _, num_days = calendar.monthrange(year, month)
    m_str = f"{year}-{month:02d}"
    scheds = {s.date: s for s in WorkSchedule.query.filter(WorkSchedule.employee_id==current_user.id, WorkSchedule.date.like(f"{m_str}%")).all()}
    m_apps = Appointment.query.filter(Appointment.employee_id==current_user.id, Appointment.date.like(f"{m_str}%"), Appointment.status!='odrzucona').order_by(Appointment.time).all()
    defs = current_user.work_days.split(',') if current_user.work_days else []
    cal_days = []
    
    today_str = now.strftime("%Y-%m-%d")
    today_schedule = {'is_working': False, 'apps': []}

    for d in range(1, num_days+1):
        dt = datetime(year, month, d); d_str = dt.strftime("%Y-%m-%d"); wd = str(dt.weekday())
        entry = scheds.get(d_str)
        iw = False; st, et = salon.open_from, salon.open_to; bs, be = "", ""
        if entry:
            iw = entry.is_working
            if iw: st, et, bs, be = entry.start_time, entry.end_time, entry.break_start or "", entry.break_end or ""
        elif wd in defs:
            iw = True
            try: 
                bj = json.loads(current_user.breaks_json)
                if wd in bj: bs, be = bj[wd].get('start', ''), bj[wd].get('end', '')
            except: pass
        
        day_apps = [{'id': a.id, 'time': a.time, 'client': a.client.username, 'service': a.service.name, 'status': a.status} for a in m_apps if a.date == d_str]
        
        if d_str == today_str:
            today_schedule = {'is_working': iw, 'start': st, 'end': et, 'apps': day_apps}

        cal_days.append({'date': d_str, 'day': d, 'is_working': iw, 'start': st, 'end': et, 'bs': bs, 'be': be, 'has_override': entry is not None, 'appointments': day_apps, 'apps_count': len(day_apps), 'is_today': (d_str == today_str)})

    prev_m = datetime(year, month, 1) - timedelta(days=1); next_m = datetime(year, month, 28) + timedelta(days=5)
    
    done = Appointment.query.filter_by(employee_id=current_user.id, status='zrealizowana').all()
    net = sum(max(0, a.service.price - (a.service.price*(salon.margin_value/100.0) if salon.margin_type=='percent' else salon.margin_value)) for a in done if a.service)
    
    slots = []
    try:
        c, e = datetime.strptime(salon.open_from, "%H:%M"), datetime.strptime(salon.open_to, "%H:%M")
        while c <= e: slots.append(c.strftime("%H:%M")); c += timedelta(minutes=5)
    except: pass

    return render_template('employee.html', 
        pending_appointments=Appointment.query.filter_by(employee_id=current_user.id, status='oczekuje').all(),
        earnings=round(net,2), salon=salon, calendar_days=cal_days, 
        nav={'py': prev_m.year, 'pm': prev_m.month, 'ny': next_m.year, 'nm': next_m.month, 'cm': month, 'cy': year},
        time_slots=slots, today_schedule=today_schedule, today_date=today_str)

@app.route('/book/date', methods=['GET', 'POST'])
@login_required
def booking_date():
    if request.method == 'POST': return redirect(url_for('booking_salon', date=request.form.get('date')))
    now = datetime.now()
    try: y, m = int(request.args.get('year', now.year)), int(request.args.get('month', now.month))
    except: y, m = now.year, now.month
    
    pl_m = {1:'Styczen',2:'Luty',3:'Marzec',4:'Kwiecien',5:'Maj',6:'Czerwiec',7:'Lipiec',8:'Sierpien',9:'Wrzesien',10:'Pazdziernik',11:'Listopad',12:'Grudzien'}
    opts = []; tmp = now
    for _ in range(12): opts.append({'name': f"{pl_m[tmp.month]} {tmp.year}", 'y': tmp.year, 'm': tmp.month, 's': (tmp.year==y and tmp.month==m)}); tmp = (datetime(tmp.year+1,1,1) if tmp.month==12 else datetime(tmp.year,tmp.month+1,1))
    
    days = []; _, n = calendar.monthrange(y, m); pl_d = {0:'Pon',1:'Wt',2:'Sr',3:'Czw',4:'Pt',5:'Sob',6:'Niedz'}
    for d in range(1, n+1):
        dt = datetime(y, m, d)
        days.append({'full_date': dt.strftime("%Y-%m-%d"), 'day_name': pl_d[dt.weekday()], 'day_num': d, 'is_weekend': dt.weekday()>=5, 'is_today': dt.date()==now.date(), 'is_past': dt.date()<now.date()})
    return render_template('booking_date.html', days=days, month_options=opts)

@app.route('/book/salon/<date>')
@login_required
def booking_salon(date):
    sd = []
    for s in Salon.query.all():
        r = db.session.query(Review).join(User, Review.employee_id == User.id).filter(User.salon_id==s.id).all()
        sd.append({'id': s.id, 'name': s.name, 'address': s.address, 'rating': round(sum(x.rating for x in r)/len(r) if r else 0, 1), 'count': len(r)})
    return render_template('booking_salon.html', date=date, salons=sd)

@app.route('/book/service/<date>/<int:salon_id>')
@login_required
def booking_service(date, salon_id): return render_template('booking_service.html', date=date, salon_id=salon_id, services=Service.query.filter_by(salon_id=salon_id).all())

@app.route('/book/employee/<date>/<int:salon_id>/<int:service_id>')
@login_required
def booking_employee(date, salon_id, service_id):
    staff = User.query.filter(User.salon_id == salon_id, User.role.in_(['pracownik', 'szef'])).all()
    dt = datetime.strptime(date, "%Y-%m-%d"); wd = str(dt.weekday()); lst = []
    for m in staff:
        revs = Review.query.filter_by(employee_id=m.id).all()
        ws = WorkSchedule.query.filter_by(employee_id=m.id, date=date).first()
        wrk = False
        if ws: wrk = ws.is_working
        else: wrk = (wd in (m.work_days.split(',') if m.work_days else []))
        
        nxt = None
        if not wrk:
            for i in range(1, 15):
                ndt = dt + timedelta(days=i); nstr = ndt.strftime("%Y-%m-%d")
                nws = WorkSchedule.query.filter_by(employee_id=m.id, date=nstr).first()
                if nws: 
                    if nws.is_working: nxt = nstr; break
                elif str(ndt.weekday()) in (m.work_days.split(',') if m.work_days else []): nxt = nstr; break

        lst.append({'user': m, 'is_working': wrk, 'next_available_date': nxt, 'rating': round(sum(r.rating for r in revs)/len(revs) if revs else 0, 1), 'reviews_count': len(revs), 'reviews_list': revs})
    return render_template('booking_employee.html', date=date, salon_id=salon_id, service_id=service_id, employees=lst)

@app.route('/book/redirect_change_date')
@login_required
def redirect_change_date(): flash('Termin niedostepny.'); return redirect(url_for('booking_date'))

@app.route('/book/time/<date>/<int:salon_id>/<int:service_id>/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def booking_time(date, salon_id, service_id, employee_id):
    s = db.session.get(Salon, salon_id); serv = db.session.get(Service, service_id); emp = db.session.get(User, employee_id)
    slots = get_slots_for_day(date, s, serv, emp)
    nxt = None
    if not slots:
        start = datetime.strptime(date, "%Y-%m-%d")
        for i in range(1, 15):
            cd = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            if get_slots_for_day(cd, s, serv, emp): nxt = cd; break
    if request.method == 'POST': db.session.add(Appointment(date=date, time=request.form.get('time'), client_id=current_user.id, employee_id=emp.id, service_id=serv.id, salon_id=s.id)); db.session.commit(); flash('Zarezerwowano!'); return redirect(url_for('client_dashboard'))
    return render_template('booking_time.html', date=date, salon=s, service=serv, employee=emp, slots=slots, next_available_date=nxt)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username='admin').first(): db.session.add(User(username='admin', password='admin', role='admin')); db.session.commit()
    app.run(debug=True)