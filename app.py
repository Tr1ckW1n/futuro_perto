import os, sqlite3, csv, io, json, secrets, unicodedata
from datetime import datetime, date, timedelta
from functools import wraps
from urllib.parse import urlparse
from openpyxl import load_workbook
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, Response, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

BASE=os.path.dirname(__file__)
app=Flask(__name__)
app.secret_key=os.getenv('SECRET_KEY') or secrets.token_hex(32)
app.config['DATABASE']=os.getenv('DATABASE_PATH',os.path.join(BASE,'futuro_perto.db'))
app.config['MAX_CONTENT_LENGTH']=5*1024*1024
# Em produção (HTTPS), os cookies só trafegam por conexão segura.
app.config['SESSION_COOKIE_SECURE']=os.getenv('COOKIE_SECURE')=='1'
app.config['SESSION_COOKIE_SAMESITE']='Lax'
STATUSES=['Inscrições abertas','Inscrições previstas','Inscrições encerradas','Consulte a instituição']
MODALITIES=['Presencial','On-line','Híbrida']
STAGES=['Rascunho','Em verificação','Publicado','Arquivado']
MAP_CITY='Goiânia'

def db():
    c=sqlite3.connect(app.config['DATABASE']); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def norm(v): return ''.join(c for c in unicodedata.normalize('NFD',str(v or '').lower()) if unicodedata.category(c)!='Mn')

def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS institutions(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,kind TEXT,website TEXT,verified INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS units(id INTEGER PRIMARY KEY,institution_id INTEGER NOT NULL REFERENCES institutions(id),name TEXT NOT NULL,city TEXT NOT NULL,address TEXT,lat REAL,lon REAL);
        CREATE TABLE IF NOT EXISTS opportunities(id INTEGER PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL,institution_id INTEGER NOT NULL REFERENCES institutions(id),unit_id INTEGER REFERENCES units(id),city TEXT NOT NULL,area TEXT NOT NULL,category TEXT NOT NULL,modality TEXT NOT NULL,hours INTEGER,requirements TEXT,audience TEXT,open_date TEXT,close_date TEXT,status TEXT NOT NULL DEFAULT 'Consulte a instituição',registration_url TEXT,source_url TEXT,verified_at TEXT,updated_at TEXT NOT NULL,keywords TEXT,stage TEXT NOT NULL DEFAULT 'Rascunho',demo INTEGER NOT NULL DEFAULT 0,editor TEXT);
        CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY,opportunity_id INTEGER REFERENCES opportunities(id),kind TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL,resolved INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS edits(id INTEGER PRIMARY KEY,opportunity_id INTEGER,editor TEXT,action TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY,username TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS visits(id INTEGER PRIMARY KEY,day TEXT NOT NULL,visitor TEXT NOT NULL,path TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS visits_day ON visits(day);
        CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY,source_name TEXT NOT NULL,imported_at TEXT NOT NULL,editor TEXT NOT NULL,created INTEGER NOT NULL,skipped INTEGER NOT NULL);
        ''')
        # Carregamento inicial automático da planilha enviada, sem publicar registros não verificados.
        if not c.execute('SELECT 1 FROM opportunities LIMIT 1').fetchone():
            seed_path=os.path.join(BASE,'cursos_iniciais.json')
            if os.path.isfile(seed_path):
                with open(seed_path,encoding='utf-8') as fp: seed=json.load(fp)
                now=date.today().isoformat()
                for item in seed:
                    c.execute('INSERT OR IGNORE INTO institutions(name,kind) VALUES(?,?)',(item['institution'],item['kind']))
                    iid=c.execute('SELECT id FROM institutions WHERE name=?',(item['institution'],)).fetchone()['id']
                    unit_id=None
                    if item['unit'] and item['city']!='Não informado' and item['modality']!='On-line':
                        u=c.execute('SELECT id FROM units WHERE institution_id=? AND name=? AND city=?',(iid,item['unit'],item['city'])).fetchone()
                        if u:unit_id=u['id']
                        else:
                            unit_id=c.execute('INSERT INTO units(institution_id,name,city,address,lat,lon) VALUES(?,?,?,?,?,?)',(iid,item['unit'],item['city'],item['address'],item['lat'],item['lon'])).lastrowid
                    notes='Importado automaticamente da planilha atualizada. Informações ainda não verificadas; confirme oferta, inscrição e endereço na instituição antes de publicar.'
                    if item.get('original_link') and not item['registration_url']:notes+=' Observação da coluna inscrição: '+item['original_link']
                    c.execute('''INSERT INTO opportunities(title,description,institution_id,unit_id,city,area,category,modality,hours,requirements,audience,status,registration_url,updated_at,keywords,stage,demo,editor)
                                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)''',
                              (item['title'],notes,iid,unit_id,item['city'],item['area'],item['category'],item['modality'],item['hours'],item['requirements'],item['audience'],'Consulte a instituição',item['registration_url'],now,'Importação inicial da planilha','Em verificação','Importação inicial'))
                c.execute('INSERT INTO imports(source_name,imported_at,editor,created,skipped) VALUES(?,?,?,?,?)',('Futuro_Perto_alimentar_atualizado.xlsx',datetime.now().isoformat(timespec='seconds'),'Importação inicial automática',len(seed),0))
        user=os.getenv('ADMIN_USER'); password=os.getenv('ADMIN_PASSWORD')
        if user and password and not c.execute('SELECT 1 FROM admins WHERE username=?',(user,)).fetchone():
            c.execute('INSERT INTO admins(username,password_hash) VALUES (?,?)',(user,generate_password_hash(password)))

def current_csrf():
    if 'csrf' not in session: session['csrf']=secrets.token_urlsafe(32)
    return session['csrf']
app.jinja_env.globals['csrf_token']=current_csrf

@app.before_request
def csrf_guard():
    if request.method in ('POST','PUT','PATCH','DELETE') and request.form.get('csrf')!=current_csrf(): abort(400,'Token de segurança inválido.')

# Contador de acessos: cookie aleatório e anônimo, sem IP nem dados pessoais.
VISIT_SKIP=('/static/','/admin','/dados')
BOTS=('bot','crawl','spider','slurp','preview','headless','monitor')

@app.after_request
def count_visit(resp):
    if request.method!='GET' or resp.status_code!=200 or not resp.mimetype=='text/html' or session.get('admin'):return resp
    if request.path.startswith(VISIT_SKIP) or any(b in (request.user_agent.string or '').lower() for b in BOTS):return resp
    vid=request.cookies.get('fp_vid')
    if not vid or len(vid)>64:
        vid=secrets.token_urlsafe(16);resp.set_cookie('fp_vid',vid,max_age=365*24*3600,httponly=True,samesite='Lax',secure=app.config['SESSION_COOKIE_SECURE'])
    with db() as c:c.execute('INSERT INTO visits(day,visitor,path) VALUES(?,?,?)',(date.today().isoformat(),vid,request.path[:200]))
    return resp

def visit_stats():
    today=date.today();since=lambda d:(today-timedelta(days=d-1)).isoformat()
    with db() as c:
        uniq=lambda d=None:c.execute('SELECT COUNT(DISTINCT visitor) FROM visits'+(' WHERE day>=?' if d else ''),(since(d),) if d else ()).fetchone()[0]
        return {'today':uniq(1),'week':uniq(7),'month':uniq(30),'total':uniq(),'views':c.execute('SELECT COUNT(*) FROM visits').fetchone()[0],
                'daily':[dict(r) for r in c.execute('SELECT day,COUNT(DISTINCT visitor) visitors,COUNT(*) views FROM visits WHERE day>=? GROUP BY day ORDER BY day DESC',(since(14),))]}

def admin_only(fn):
    @wraps(fn)
    def f(*a,**kw):
        if not session.get('admin'): return redirect(url_for('login',next=request.path))
        return fn(*a,**kw)
    return f

def get_filters(rows):
    return {field:sorted(set(r[field] for r in rows if r[field]),key=norm) for field in ('city','area','modality','status','category','institution')}

def all_public():
    with db() as c:
        return [dict(r) for r in c.execute('''SELECT o.*,i.name institution,i.website institution_website,u.name unit,u.address,u.lat,u.lon FROM opportunities o JOIN institutions i ON i.id=o.institution_id LEFT JOIN units u ON u.id=o.unit_id WHERE o.stage='Publicado' ORDER BY o.updated_at DESC,o.id DESC''')]

def filtered():
    rows=all_public(); fs={k:request.args.get(k,'').strip() for k in ('q','city','area','modality','status','category','institution')}
    def valid(r):
        if fs['q'] and norm(fs['q']) not in norm(' '.join(str(r.get(k) or '') for k in ('title','description','city','area','institution','keywords'))):return False
        return all(not fs[k] or r.get(k)==fs[k] for k in fs if k!='q')
    rows=[r for r in rows if valid(r)]
    order=request.args.get('order','recent')
    if order=='title': rows.sort(key=lambda r:norm(r['title']))
    elif order=='deadline': rows.sort(key=lambda r:(not bool(r['close_date']),r['close_date'] or '9999'))
    return rows,fs

@app.context_processor
def helpers(): return {'year':date.today().year,'admin_logged':bool(session.get('admin')),'demo_present':any(r['demo'] for r in all_public())}

@app.get('/')
def home():
    rows=all_public();georows=[r for r in rows if norm(r['city'])==norm(MAP_CITY) and r['lat'] is not None and r['lon'] is not None and not r['demo']]
    return render_template('home.html',featured=rows[:6],categories=sorted(set(r['area'] for r in rows),key=norm),cities=sorted(set(r['city'] for r in rows),key=norm),count=len(rows),georows=georows,map_city=MAP_CITY)

@app.get('/explorar')
def explore():
    rows,fs=filtered();page=max(1,request.args.get('page',1,type=int));per=9;total=len(rows);subset=rows[(page-1)*per:page*per]
    return render_template('explore.html',rows=subset,allcount=total,filters=get_filters(all_public()),fs=fs,page=page,pages=(total+per-1)//per,order=request.args.get('order','recent'))

@app.get('/mapa')
def map_page():
    # O mapa cobre apenas Goiânia.
    rows,fs=filtered();rows=[r for r in rows if norm(r['city'])==norm(MAP_CITY)]
    return render_template('map.html',rows=rows,georows=[r for r in rows if r['lat'] is not None and r['lon'] is not None and not r['demo']],filters=get_filters([r for r in all_public() if norm(r['city'])==norm(MAP_CITY)]),fs=fs,map_city=MAP_CITY)

@app.get('/oportunidade/<int:oid>')
def detail(oid):
    r=next((r for r in all_public() if r['id']==oid),None)
    if not r:abort(404)
    return render_template('detail.html',r=r)

@app.get('/como-funciona')
def how():return render_template('info.html',heading='Como funciona',paragraphs=['Descubra: pesquise por município, área ou instituição, sem precisar criar conta.','Compare: consulte modalidade, requisitos, duração e situação informada das inscrições.','Participe: confira a fonte e siga o link oficial, quando disponível. A seleção e a inscrição pertencem à instituição responsável.'])
@app.get('/sobre')
def about():return render_template('info.html',heading='Sobre o Futuro Perto',paragraphs=['O Futuro Perto reúne e organiza informações de oportunidades gratuitas de formação profissional em Goiás.','O projeto facilita a descoberta, mas não oferece cursos, não faz inscrições e não estabelece parcerias institucionais por conta própria.','Informações demonstrativas aparecem identificadas e não devem ser usadas para inscrição.'])
@app.get('/privacidade')
def privacy():return render_template('info.html',heading='Privacidade',paragraphs=['Consultar oportunidades não exige login, CPF ou localização pessoal.','A área administrativa utiliza sessão para autenticação. Relatos enviados ficam registrados na base de dados, sem exigir dados pessoais.','Para contar visitantes, o site grava um cookie com um código aleatório e anônimo. Não registramos IP, nome ou localização; apenas o dia e a página acessada.','Uma implantação pública exige revisão da política conforme infraestrutura, hospedagem, retenção e obrigações da LGPD.'])
@app.get('/acessibilidade')
def accessibility():return render_template('info.html',heading='Acessibilidade',paragraphs=['O site oferece campos rotulados, navegação por teclado, foco visível e listagem textual como alternativa ao mapa.','A conformidade integral com WCAG 2.2 AA depende de auditoria e testes adicionais antes da publicação.'])
@app.get('/criterios')
def criteria():return render_template('info.html',heading='Critérios de atualização',paragraphs=['Só aparecem publicamente oportunidades marcadas como publicadas. O status das inscrições depende de confirmação, não de dedução automática por datas.','As fontes, datas de verificação e links devem ser inseridos pela equipe responsável. Registros com data vencida ou verificação antiga são sinalizados para revisão.','Amostras demonstrativas não são ofertas oficiais, não devem orientar inscrições e não representam números de cobertura real.'])

@app.route('/contato',methods=['GET','POST'])
def contact():
    if request.method=='POST':
        kind=request.form.get('kind','Correção')[:60];msg=request.form.get('message','').strip(); oid=request.form.get('opportunity_id',type=int)
        if len(msg)<10 or len(msg)>3000:flash('Informe uma mensagem entre 10 e 3.000 caracteres.','error')
        else:
            with db() as c: c.execute('INSERT INTO reports(opportunity_id,kind,message,created_at) VALUES(?,?,?,?)',(oid,kind,msg,datetime.now().isoformat(timespec='seconds')))
            flash('Comunicação registrada. Obrigado por contribuir.','success');return redirect(url_for('contact'))
    return render_template('contact.html',oid=request.args.get('oportunidade',''))

@app.get('/dados.json')
def data_json():return jsonify([public_record(r) for r in all_public() if not r['demo']])
def public_record(r):return {k:r[k] for k in ('id','title','description','city','area','category','modality','hours','status','open_date','close_date','registration_url','source_url','verified_at','updated_at','institution')}
@app.get('/dados.csv')
def data_csv():
    rows=[public_record(r) for r in all_public() if not r['demo']];output=io.StringIO();fields=['id','title','description','city','area','category','modality','hours','status','open_date','close_date','registration_url','source_url','verified_at','updated_at','institution'];w=csv.DictWriter(output,fieldnames=fields);w.writeheader();w.writerows(rows)
    return Response('\ufeff'+output.getvalue(),mimetype='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=futuro-perto-dados.csv'})

@app.route('/admin/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        u=request.form.get('username','');p=request.form.get('password','')
        with db() as c:r=c.execute('SELECT * FROM admins WHERE username=?',(u,)).fetchone()
        if r and check_password_hash(r['password_hash'],p):
            session.clear();session['admin']=u;current_csrf();return redirect(url_for('admin'))
        flash('Usuário ou senha inválidos.','error')
    return render_template('login.html')
@app.post('/admin/logout')
@admin_only
def logout():session.clear();return redirect(url_for('home'))

@app.get('/admin')
@admin_only
def admin():
    with db() as c:
        rows=[dict(r) for r in c.execute('SELECT o.*,i.name institution FROM opportunities o JOIN institutions i ON i.id=o.institution_id ORDER BY o.updated_at DESC')]
        reports=[dict(r) for r in c.execute('SELECT * FROM reports ORDER BY created_at DESC LIMIT 50')]
        inst=[dict(r) for r in c.execute('SELECT * FROM institutions ORDER BY name')]
        units=[dict(r) for r in c.execute('SELECT u.*,i.name institution FROM units u JOIN institutions i ON i.id=u.institution_id ORDER BY city,name')]
    today=date.today();review=[r for r in rows if r['stage']=='Publicado' and ((r['close_date'] and r['close_date']<today.isoformat()) or not r['verified_at'] or (date.fromisoformat(r['verified_at']) < today-timedelta(days=90)))]
    return render_template('admin.html',rows=rows,reports=reports,institutions=inst,units=units,review_ids=set(r['id'] for r in review),imports=[dict(r) for r in db().execute('SELECT * FROM imports ORDER BY id DESC LIMIT 8')],visits=visit_stats())

def import_text(value):
    return str(value).strip() if value is not None else ''

def import_modality(raw):
    x=norm(raw)
    if 'hibrid' in x or ('presencial' in x and ('ead' in x or 'on-line' in x or 'online' in x)): return 'Híbrida'
    if 'ead' in x or 'on-line' in x or 'online' in x or 'virtual' in x: return 'On-line'
    if 'presencial' in x: return 'Presencial'
    return None

@app.post('/admin/importar')
@admin_only
def import_sheet():
    upload=request.files.get('arquivo')
    if not upload or not upload.filename or not upload.filename.lower().endswith('.xlsx'):
        flash('Selecione um arquivo Excel .xlsx.','error');return redirect(url_for('admin'))
    try:
        workbook=load_workbook(upload.stream,read_only=True,data_only=True)
        if 'COLETA' not in workbook.sheetnames: raise ValueError('É necessária uma aba COLETA.')
        sheet=workbook['COLETA']; iterator=sheet.iter_rows(values_only=True)
        header=[norm(import_text(v)) for v in next(iterator)]
        def get(row,name):
            name=norm(name);return import_text(row[header.index(name)]) if name in header and len(row)>header.index(name) else ''
        required=['Nome do curso/oportunidade','Instituição','Área de formação','Categoria']
        if any(norm(k) not in header for k in required):raise ValueError('Cabeçalhos necessários ausentes na aba COLETA.')
        made=skipped=0
        with db() as c:
            for row in iterator:
                title=get(row,'Nome do curso/oportunidade');institution=get(row,'Instituição')
                if not title or not institution:continue
                source=get(row,'FONTE_URL (de onde veio o dado)'); link=get(row,'Link oficial de inscrição')
                if (source and not safe_url(source)) or (link and not safe_url(link)):
                    skipped+=1;continue
                candidate=c.execute('SELECT id FROM institutions WHERE name=?',(institution,)).fetchone()
                if candidate:iid=candidate['id']
                else:iid=c.execute('INSERT INTO institutions(name,kind) VALUES(?,?)', (institution, get(row,'Tipo (pública/Sistema S/federal/ONG'))).lastrowid
                # Duplicate matching course+provider+source; subsequent imports do not overwrite editorial work.
                if c.execute("SELECT 1 FROM opportunities WHERE title=? AND institution_id=? AND COALESCE(source_url, '')=?",(title,iid,source)).fetchone():
                    skipped+=1;continue
                modality=import_modality(get(row,'Modalidade')) or 'On-line'
                original_mod=get(row,'Modalidade');city=get(row,'Município') or get(row,'Cidade')
                if not city:city='Não informado'
                area=get(row,'Área de formação') or 'A verificar';category=get(row,'Categoria') or 'Curso'
                description='Registro importado da planilha de coleta, ainda não verificado. Confirme requisitos, gratuidade, modalidade, município, fonte e condições de inscrição antes de publicar.'
                if not import_modality(original_mod):description+=' Modalidade original: '+(original_mod or 'não informada')+'.'
                hours_text=get(row,'Carga horária');hours=int(hours_text) if hours_text.isdigit() else None
                requirements='Carga horária na planilha: '+hours_text+'.' if hours_text and not hours else ''
                requirements+=(' Gratuidade na planilha: '+get(row,'Condição de gratuidade')+'.') if get(row,'Condição de gratuidade') else ''
                now=date.today().isoformat()
                c.execute('''INSERT INTO opportunities(title,description,institution_id,city,area,category,modality,hours,requirements,audience,status,registration_url,source_url,updated_at,keywords,stage,demo,editor)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)''',
                          (title,description,iid,city,area,category,modality,hours,requirements,get(row,'Público-alvo'),'Consulte a instituição',link,source,now,'Origem: planilha de coleta; prazo original: '+get(row,'Prazo inscrição')+'; situação original: '+get(row,'Situação')+'; gratuidade: '+get(row,'Gratuidade confirmada?'),'Em verificação',session['admin']))
                made+=1
            c.execute('INSERT INTO imports(source_name,imported_at,editor,created,skipped) VALUES(?,?,?,?,?)',(upload.filename[:200],datetime.now().isoformat(timespec='seconds'),session['admin'],made,skipped))
        flash(f'Importação concluída: {made} registros em verificação; {skipped} ignorados (duplicados ou URLs inválidas).','success')
    except Exception as e:
        flash('Erro na importação: '+str(e)[:220],'error')
    return redirect(url_for('admin'))

@app.get('/admin/modelo-importacao')
@admin_only
def import_instructions():
    return Response('Use a aba COLETA do arquivo Futuro_Perto_base_coleta_v1.xlsx, com pelo menos as colunas Nome do curso/oportunidade, Instituição, Área de formação e Categoria. Registros entram em verificação e não são publicados automaticamente.',mimetype='text/plain; charset=utf-8')

@app.post('/admin/institutions')
@admin_only
def add_institution():
    name=request.form.get('name','').strip();website=request.form.get('website','').strip()
    if not name:flash('Informe o nome da instituição.','error')
    elif website and not safe_url(website):flash('URL inválida.','error')
    else:
        with db() as c:c.execute('INSERT OR IGNORE INTO institutions(name,kind,website) VALUES(?,?,?)',(name,request.form.get('kind',''),website))
        flash('Instituição cadastrada.','success')
    return redirect(url_for('admin'))

@app.post('/admin/units')
@admin_only
def add_unit():
    try:
        iid=int(request.form['institution_id']);name=request.form['name'].strip();city=request.form['city'].strip();lat=float(request.form['lat']) if request.form.get('lat') else None;lon=float(request.form['lon']) if request.form.get('lon') else None
        if not name or not city or (lat is None)!=(lon is None) or (lat is not None and not(-90<=lat<=90 and -180<=lon<=180)):raise ValueError()
        with db() as c:c.execute('INSERT INTO units(institution_id,name,city,address,lat,lon) VALUES(?,?,?,?,?,?)',(iid,name,city,request.form.get('address',''),lat,lon))
        flash('Unidade cadastrada.','success')
    except (ValueError,KeyError,sqlite3.IntegrityError):flash('Confira os dados da unidade e as coordenadas.','error')
    return redirect(url_for('admin'))

def safe_url(url):
    try:r=urlparse(url);return r.scheme=='https' and bool(r.netloc) and not r.username and not r.password
    except ValueError:return False

@app.route('/admin/oportunidade/nova',methods=['GET','POST'])
@app.route('/admin/oportunidade/<int:oid>/editar',methods=['GET','POST'])
@admin_only
def edit(oid=None):
    with db() as c:
        institutions=[dict(x) for x in c.execute('SELECT * FROM institutions ORDER BY name')]
        units=[dict(x) for x in c.execute('SELECT * FROM units ORDER BY city,name')]
        existing=dict(c.execute('SELECT * FROM opportunities WHERE id=?',(oid,)).fetchone()) if oid and c.execute('SELECT 1 FROM opportunities WHERE id=?',(oid,)).fetchone() else None
    if oid and not existing:abort(404)
    if request.method=='POST':
        x={k:request.form.get(k,'').strip() for k in ('title','description','city','area','category','modality','requirements','audience','open_date','close_date','status','registration_url','source_url','verified_at','keywords','stage')}
        try:
            iid=int(request.form['institution_id']);unitid=int(request.form['unit_id']) if request.form.get('unit_id') else None
            hours=int(request.form['hours']) if request.form.get('hours') else None
            if not all(x[k] for k in ('title','description','city','area','category')) or x['modality'] not in MODALITIES or x['status'] not in STATUSES or x['stage'] not in STAGES:raise ValueError('Campos obrigatórios inválidos.')
            if hours is not None and hours<=0:raise ValueError('Carga horária inválida.')
            if any(x[k] and not safe_url(x[k]) for k in ('registration_url','source_url')):raise ValueError('Links devem começar com https://')
            for k in ('open_date','close_date','verified_at'):
                if x[k]:date.fromisoformat(x[k])
            if x['stage']=='Publicado' and (not x['source_url'] or not x['verified_at'] or x['city']=='Não informado') and not request.form.get('demo'):raise ValueError('Publicar exige fonte oficial e data de verificação.')
            if unitid:
                with db() as c:u=c.execute('SELECT * FROM units WHERE id=? AND institution_id=?',(unitid,iid)).fetchone()
                if not u:raise ValueError('Unidade e instituição não correspondem.')
                if x['city']!=u['city']:raise ValueError('O município deve corresponder ao município da unidade.')
            cols=['title','description','institution_id','unit_id','city','area','category','modality','hours','requirements','audience','open_date','close_date','status','registration_url','source_url','verified_at','updated_at','keywords','stage','demo','editor']
            vals=[x['title'],x['description'],iid,unitid,x['city'],x['area'],x['category'],x['modality'],hours,x['requirements'],x['audience'],x['open_date'] or None,x['close_date'] or None,x['status'],x['registration_url'],x['source_url'],x['verified_at'] or None,date.today().isoformat(),x['keywords'],x['stage'],int(bool(request.form.get('demo'))),session['admin']]
            with db() as c:
                if oid:c.execute('UPDATE opportunities SET '+','.join(k+'=?' for k in cols)+' WHERE id=?',vals+[oid]);newid=oid
                else:
                    cur=c.execute('INSERT INTO opportunities('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',vals);newid=cur.lastrowid
                c.execute('INSERT INTO edits(opportunity_id,editor,action,created_at) VALUES(?,?,?,?)',(newid,session['admin'],'Atualização' if oid else 'Criação',datetime.now().isoformat(timespec='seconds')))
            flash('Oportunidade salva.','success');return redirect(url_for('admin'))
        except (ValueError,KeyError,sqlite3.IntegrityError) as e:flash(str(e) or 'Confira os campos.','error')
    return render_template('edit.html',r=existing,institutions=institutions,units=units,statuses=STATUSES,modalities=MODALITIES,stages=STAGES)

@app.post('/admin/reports/<int:rid>/resolve')
@admin_only
def resolve(rid):
    with db() as c:c.execute('UPDATE reports SET resolved=1 WHERE id=?',(rid,))
    return redirect(url_for('admin'))

@app.errorhandler(404)
def notfound(e):return render_template('info.html',heading='Página não encontrada',paragraphs=['O endereço solicitado não está disponível. Volte ao início para explorar as oportunidades.']),404

init_db()
if __name__=='__main__':app.run(debug=os.getenv('FLASK_DEBUG')=='1',host='127.0.0.1',port=int(os.getenv('PORT','5000')))
