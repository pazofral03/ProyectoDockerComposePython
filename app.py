import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io
import base64
import os
import json
import subprocess
from datetime import datetime, timedelta

# --- AQUÍ ESTABA EL ERROR ---
# Tienes que asegurarte de que 'send_file' esté en esta lista:
from flask import Flask, render_template, request, redirect, url_for, flash, send_file

# Y también asegurarte de que tienes esto para el PDF:
from matplotlib.backends.backend_pdf import PdfPages
from pymongo import MongoClient
from bson.objectid import ObjectId
from bson import json_util

app = Flask(__name__)
app.secret_key = 'super_secret_key'

# --- CONFIGURACIÓN ---
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)
db = client['mi_negocio']
collection = db['ventas']
BACKUP_FILE = 'datos_backup.json'

# --- FUNCIONES AUXILIARES (BACKUP Y GIT - SIN CAMBIOS) ---
def cargar_datos_desde_json():
    if os.path.exists(BACKUP_FILE):
        try:
            with open(BACKUP_FILE, 'r') as file:
                data = json.load(file, object_hook=json_util.object_hook)
                if data:
                    collection.insert_many(data)
                    print(f"--- DATOS RESTAURADOS DESDE {BACKUP_FILE} ---")
        except Exception as e:
            print(f"Error cargando JSON: {e}")

def guardar_datos_en_json():
    datos = list(collection.find())
    with open(BACKUP_FILE, 'w') as file:
        json.dump(datos, file, default=json_util.default, indent=4)
    print("--- BACKUP JSON CREADO ---")

def ejecutar_git_push():
    usuario = os.environ.get('GITHUB_USER')
    token = os.environ.get('GITHUB_TOKEN')
    repo_url = os.environ.get('GITHUB_REPO')

    if not usuario or not token or not repo_url:
        return False, "Error: Faltan variables de entorno GITHUB_..."

    auth_remote_url = f"https://{usuario}:{token}@{repo_url}"

    try:
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "/app"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "bot@docker.local"], check=False)
        subprocess.run(["git", "config", "--global", "user.name", "Docker Backup Bot"], check=False)

        if not os.path.exists(".git"):
            subprocess.run(["git", "init"], check=True)
            subprocess.run(["git", "branch", "-M", "main"], check=True)
        
        subprocess.run(["git", "remote", "remove", "origin"], capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", auth_remote_url], check=True)

        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-m", "Auto-sync: Código y Datos actualizados desde Docker"], check=False)
        
        result = subprocess.run(["git", "push", "-u", "origin", "main"], capture_output=True, text=True)
        
        if result.returncode == 0:
            return True, "Sincronización COMPLETA exitosa."
        else:
            return False, f"Error en Push: {result.stderr}"

    except Exception as e:
        return False, str(e)

def fig_to_base64(fig):
    img = io.BytesIO()
    plt.savefig(img, format='png', bbox_inches='tight')
    img.seek(0)
    data = base64.b64encode(img.getvalue()).decode()
    plt.close(fig)
    return data

# --- LÓGICA DE NEGOCIO ---

def obtener_kpis(ventas):
    if not ventas: return None
    total_ingresos = sum(d['ingresos'] for d in ventas)
    ticket_medio = total_ingresos / len(ventas)
    prod_ingresos = {}
    for v in ventas:
        prod_ingresos[v['producto']] = prod_ingresos.get(v['producto'], 0) + v['ingresos']
    top_producto = max(prod_ingresos, key=prod_ingresos.get)
    return {
        "total_ingresos": f"{total_ingresos:,.2f}",
        "ticket_medio": f"{ticket_medio:,.2f}",
        "top_producto": top_producto
    }

def preparar_datos_tarta(ventas):
    agrupado = {}
    for v in ventas:
        prod = v['producto']
        agrupado[prod] = agrupado.get(prod, 0) + v['ingresos']
    lista_ordenada = sorted(agrupado.items(), key=lambda x: x[1], reverse=True)
    
    if len(lista_ordenada) > 5:
        top_5 = lista_ordenada[:5]
        otros_valor = sum(item[1] for item in lista_ordenada[5:])
        top_5.append(('Otros', otros_valor))
        labels = [x[0] for x in top_5]
        values = [x[1] for x in top_5]
    else:
        labels = [x[0] for x in lista_ordenada]
        values = [x[1] for x in lista_ordenada]
    return labels, values

# --- RUTAS ---
@app.route('/')
def dashboard():
    # 1. Recuperar filtros
    filtro_tiempo = request.args.get('tiempo', 'todo')
    filtro_orden = request.args.get('orden', 'cantidad')

    # 2. Obtener datos
    todas_ventas = list(collection.find())
    for v in todas_ventas:
        if 'fecha' not in v: v['fecha'] = datetime.now()

    # 3. Filtrado
    if filtro_tiempo == '30dias':
        fecha_corte = datetime.now() - timedelta(days=30)
        ventas = [v for v in todas_ventas if v['fecha'] >= fecha_corte]
    else:
        ventas = todas_ventas

    if not ventas:
        return render_template('dashboard.html', kpis=None, 
                               plot_barras=None, plot_tarta=None, 
                               plot_pareto=None, plot_timeline=None,
                               filtro_tiempo=filtro_tiempo, filtro_orden=filtro_orden)

    kpis = obtener_kpis(ventas)
    ventas_por_fecha = sorted(ventas, key=lambda x: x['fecha'])

    # --- CONFIGURACIÓN VISUAL ---
    plt.style.use('ggplot')
    todos_productos = sorted(list(set(v['producto'] for v in todas_ventas)))
    paleta = plt.cm.tab20(range(len(todos_productos)))
    product_colors = {prod: paleta[i % 20] for i, prod in enumerate(todos_productos)}
    product_colors['Otros'] = '#d3d3d3' 

    # ==========================================
    # GRÁFICO 1: BARRAS
    # ==========================================
    fig1, ax1 = plt.subplots(figsize=(6, 4))
    
    prod_cantidad = {}
    prod_ingresos = {}
    for v in ventas:
        prod_cantidad[v['producto']] = prod_cantidad.get(v['producto'], 0) + v['cantidad']
        prod_ingresos[v['producto']] = prod_ingresos.get(v['producto'], 0) + v['ingresos']
    
    if filtro_orden == 'ingresos':
        lista_ordenada = sorted(prod_cantidad.items(), key=lambda item: prod_ingresos.get(item[0], 0), reverse=True)
        titulo_barras = 'Unidades (Ordenado por Rentabilidad)'
    else:
        lista_ordenada = sorted(prod_cantidad.items(), key=lambda item: item[1], reverse=True)
        titulo_barras = 'Unidades (Ordenado por Volumen)'

    lista_prods = [x[0] for x in lista_ordenada]
    lista_vals = [x[1] for x in lista_ordenada]
    colores = [product_colors.get(p, '#333') for p in lista_prods]
    
    ax1.bar(lista_prods, lista_vals, color=colores)
    ax1.set_title(titulo_barras, fontsize=10, weight='bold')
    
    # +++ ETIQUETAS AÑADIDAS +++
    ax1.set_xlabel('Productos', fontsize=9, color='#555')
    ax1.set_ylabel('Cantidad Vendida', fontsize=9, color='#555')
    # ++++++++++++++++++++++++++
    
    ax1.tick_params(axis='x', rotation=45, labelsize=8)
    plot_barras = fig_to_base64(fig1)

    # ==========================================
    # GRÁFICO 2: TARTA (Sin ejes X/Y)
    # ==========================================
    fig2, ax2 = plt.subplots(figsize=(6, 4))
    labels_pie, values_pie = preparar_datos_tarta(ventas)
    colores_pie = [product_colors.get(l, '#d3d3d3') for l in labels_pie]
    explode = [0.1] + [0]*(len(values_pie)-1) if values_pie else None
    
    ax2.pie(values_pie, labels=labels_pie, autopct='%1.1f%%', startangle=140, 
            explode=explode, shadow=True, colors=colores_pie)
    ax2.set_title('Distribución Ingresos', fontsize=10, weight='bold')
    plot_tarta = fig_to_base64(fig2)

    # ==========================================
    # GRÁFICO 3: PARETO
    # ==========================================
    fig3, ax3 = plt.subplots(figsize=(6, 4))
    ingresos_por_prod = {}
    for v in ventas:
        ingresos_por_prod[v['producto']] = ingresos_por_prod.get(v['producto'], 0) + v['ingresos']
    sorted_par = sorted(ingresos_por_prod.items(), key=lambda x: x[1], reverse=True)
    prods_par = [x[0] for x in sorted_par]
    ingr_par = [x[1] for x in sorted_par]
    
    colores_par = [product_colors.get(p, '#333') for p in prods_par]
    total = sum(ingr_par)
    acumulado = [sum(ingr_par[:i+1])/total*100 for i in range(len(ingr_par))]

    ax3.bar(prods_par, ingr_par, color=colores_par)
    
    # +++ ETIQUETAS EJE PRIMARIO (Izquierda) +++
    ax3.set_xlabel('Productos', fontsize=9, color='#555')
    ax3.set_ylabel('Ingresos Totales (€)', fontsize=9, color='#555')
    # ++++++++++++++++++++++++++++++++++++++++++

    ax3_twin = ax3.twinx()
    ax3_twin.plot(prods_par, acumulado, color='red', marker='o', linewidth=2)
    ax3_twin.axhline(80, color='gray', linestyle='--')
    
    # +++ ETIQUETA EJE SECUNDARIO (Derecha) +++
    ax3_twin.set_ylabel('% Acumulado', fontsize=9, color='red')
    # +++++++++++++++++++++++++++++++++++++++++
    
    ax3.set_title('Pareto (80/20)', fontsize=10, weight='bold')
    ax3.tick_params(axis='x', rotation=45, labelsize=8)
    plot_pareto = fig_to_base64(fig3)

    # ==========================================
    # GRÁFICO 4: TIMELINE
    # ==========================================
    fig4, ax4 = plt.subplots(figsize=(6, 4))
    fechas_map = {}
    for v in ventas_por_fecha:
        dia = v['fecha'].date()
        fechas_map[dia] = fechas_map.get(dia, 0) + v['ingresos']
    fechas_ord = sorted(fechas_map.keys())
    vals_tiempo = [fechas_map[d] for d in fechas_ord]
    
    ax4.plot(fechas_ord, vals_tiempo, marker='o', linestyle='-', color='#2ca02c')
    ax4.set_title('Tendencia Temporal', fontsize=10, weight='bold')
    
    # +++ ETIQUETAS AÑADIDAS +++
    ax4.set_xlabel('Fecha de Venta', fontsize=9, color='#555')
    ax4.set_ylabel('Facturación Diaria (€)', fontsize=9, color='#555')
    # ++++++++++++++++++++++++++

    ax4.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax4.tick_params(axis='x', rotation=45)
    plot_timeline = fig_to_base64(fig4)

    return render_template('dashboard.html', kpis=kpis, 
                           plot_barras=plot_barras, 
                           plot_tarta=plot_tarta, 
                           plot_pareto=plot_pareto, 
                           plot_timeline=plot_timeline,
                           filtro_tiempo=filtro_tiempo,
                           filtro_orden=filtro_orden)



# ... (RESTO DE RUTAS: gestion, agregar, eliminar, sincronizar, editar, actualizar IGUALES) ...
@app.route('/gestion')
def gestion():
    ventas = list(collection.find().sort("fecha", -1))
    return render_template('gestion.html', ventas=ventas)

@app.route('/agregar', methods=['POST'])
def agregar():
    fecha_str = request.form['fecha']
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d')
    except ValueError:
        fecha_obj = datetime.now()

    nuevo_dato = {
        "producto": request.form['producto'],
        "cantidad": int(request.form['cantidad']),
        "ingresos": float(request.form['ingresos']),
        "fecha": fecha_obj
    }
    collection.insert_one(nuevo_dato)
    return redirect(url_for('gestion'))

@app.route('/eliminar/<id>')
def eliminar(id):
    collection.delete_one({'_id': ObjectId(id)})
    return redirect(url_for('gestion'))

@app.route('/editar/<id>')
def editar(id):
    venta = collection.find_one({'_id': ObjectId(id)})
    return render_template('editar.html', venta=venta)

@app.route('/actualizar/<id>', methods=['POST'])
def actualizar(id):
    fecha_str = request.form['fecha']
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d')
    except ValueError:
        fecha_obj = datetime.now()

    datos_actualizados = {
        "producto": request.form['producto'],
        "cantidad": int(request.form['cantidad']),
        "ingresos": float(request.form['ingresos']),
        "fecha": fecha_obj
    }
    collection.update_one({'_id': ObjectId(id)}, {'$set': datos_actualizados})
    flash('Registro actualizado correctamente', 'success')
    return redirect(url_for('gestion'))

@app.route('/sincronizar')
def sincronizar():
    guardar_datos_en_json()
    exito, mensaje = ejecutar_git_push()
    if exito:
        flash(f'Éxito: {mensaje}', 'success')
    else:
        flash(f'Backup creado localmente, pero falló el Push: {mensaje}', 'warning')
    return redirect(url_for('gestion'))

@app.route('/reporte_pdf')
def reporte_pdf():
    # 1. Recuperar filtros (para que el PDF coincida con lo que ves en pantalla)
    filtro_tiempo = request.args.get('tiempo', 'todo')
    filtro_orden = request.args.get('orden', 'cantidad')

    # 2. Obtener y Filtrar Datos (Igual que en dashboard)
    todas_ventas = list(collection.find())
    for v in todas_ventas:
        if 'fecha' not in v: v['fecha'] = datetime.now()

    if filtro_tiempo == '30dias':
        fecha_corte = datetime.now() - timedelta(days=30)
        ventas = [v for v in todas_ventas if v['fecha'] >= fecha_corte]
    else:
        ventas = todas_ventas

    if not ventas:
        flash("No hay datos para generar el PDF", "warning")
        return redirect(url_for('dashboard'))

    # 3. Preparar Datos y Colores
    kpis = obtener_kpis(ventas)
    ventas_por_fecha = sorted(ventas, key=lambda x: x['fecha'])
    
    plt.style.use('ggplot')
    todos_productos = sorted(list(set(v['producto'] for v in todas_ventas)))
    paleta = plt.cm.tab20(range(len(todos_productos)))
    product_colors = {prod: paleta[i % 20] for i, prod in enumerate(todos_productos)}
    product_colors['Otros'] = '#d3d3d3'

    # 4. CREAR EL PDF EN MEMORIA
    buffer = io.BytesIO()
    
    with PdfPages(buffer) as pdf:
        
        # --- PÁGINA 1: PORTADA Y KPIs ---
        fig_portada = plt.figure(figsize=(8.5, 11)) # Tamaño Carta/A4 aprox
        fig_portada.clf()
        
        # Título
        txt_titulo = f"Informe de Ventas\nGenerated by Docker & Python"
        fig_portada.text(0.5, 0.9, txt_titulo, ha='center', fontsize=24, weight='bold', color='#333')
        
        # Fecha de emisión
        fig_portada.text(0.5, 0.85, f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ha='center', fontsize=12, color='#666')
        
        # KPIs en texto grande
        y_pos = 0.7
        fig_portada.text(0.5, y_pos, "RESUMEN EJECUTIVO", ha='center', fontsize=16, weight='bold', color='#0d6efd')
        
        metrics = [
            f"Ingresos Totales: {kpis['total_ingresos']} €",
            f"Ticket Medio: {kpis['ticket_medio']} €",
            f"Producto Top: {kpis['top_producto']}",
            f"Total Ventas Registradas: {len(ventas)}"
        ]
        
        for i, metric in enumerate(metrics):
            fig_portada.text(0.5, y_pos - 0.1 - (i*0.05), metric, ha='center', fontsize=14)

        # Nota de filtros
        fig_portada.text(0.5, 0.4, f"Filtros aplicados: Tiempo={filtro_tiempo} | Orden={filtro_orden}", 
                         ha='center', fontsize=10, style='italic', color='gray')

        pdf.savefig(fig_portada)
        plt.close()

        # --- PÁGINA 2: GRÁFICOS DE BARRAS Y TARTA ---
        fig_pg2, (ax1, ax2) = plt.subplots(2, 1, figsize=(8.5, 11))
        
        # Barras (Reutilizamos lógica)
        prod_cantidad = {}
        prod_ingresos = {}
        for v in ventas:
            prod_cantidad[v['producto']] = prod_cantidad.get(v['producto'], 0) + v['cantidad']
            prod_ingresos[v['producto']] = prod_ingresos.get(v['producto'], 0) + v['ingresos']
        
        if filtro_orden == 'ingresos':
            lista_ordenada = sorted(prod_cantidad.items(), key=lambda item: prod_ingresos.get(item[0], 0), reverse=True)
            titulo_barras = 'Unidades (Por Rentabilidad)'
        else:
            lista_ordenada = sorted(prod_cantidad.items(), key=lambda item: item[1], reverse=True)
            titulo_barras = 'Unidades (Por Volumen)'

        list_p = [x[0] for x in lista_ordenada]
        list_v = [x[1] for x in lista_ordenada]
        cols = [product_colors.get(p, '#333') for p in list_p]
        
        ax1.bar(list_p, list_v, color=cols)
        ax1.set_title(titulo_barras)
        ax1.tick_params(axis='x', rotation=45, labelsize=8)
        
        # Tarta
        labels_pie, values_pie = preparar_datos_tarta(ventas)
        cols_pie = [product_colors.get(l, '#d3d3d3') for l in labels_pie]
        explode = [0.1] + [0]*(len(values_pie)-1) if values_pie else None
        ax2.pie(values_pie, labels=labels_pie, autopct='%1.1f%%', startangle=140, explode=explode, colors=cols_pie)
        ax2.set_title('Distribución de Ingresos')
        
        plt.tight_layout(pad=5.0) # Espacio para que no se peguen
        pdf.savefig(fig_pg2)
        plt.close()

        # --- PÁGINA 3: PARETO Y TIMELINE ---
        fig_pg3, (ax3, ax4) = plt.subplots(2, 1, figsize=(8.5, 11))
        
        # Pareto
        ingr_prod = {}
        for v in ventas: ingr_prod[v['producto']] = ingr_prod.get(v['producto'], 0) + v['ingresos']
        sorted_par = sorted(ingr_prod.items(), key=lambda x: x[1], reverse=True)
        pp = [x[0] for x in sorted_par]
        ip = [x[1] for x in sorted_par]
        cp = [product_colors.get(p, '#333') for p in pp]
        tot = sum(ip)
        acum = [sum(ip[:i+1])/tot*100 for i in range(len(ip))]

        ax3.bar(pp, ip, color=cp)
        ax3t = ax3.twinx()
        ax3t.plot(pp, acum, color='red', marker='o')
        ax3t.axhline(80, color='gray', linestyle='--')
        ax3.set_title('Pareto')
        ax3.tick_params(axis='x', rotation=45, labelsize=8)

        # Timeline
        f_map = {}
        for v in ventas_por_fecha:
            d = v['fecha'].date()
            f_map[d] = f_map.get(d, 0) + v['ingresos']
        ford = sorted(f_map.keys())
        vtiem = [f_map[d] for d in ford]
        
        ax4.plot(ford, vtiem, marker='o', color='green')
        ax4.set_title('Evolución Temporal')
        ax4.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
        ax4.tick_params(axis='x', rotation=45)

        plt.tight_layout(pad=5.0)
        pdf.savefig(fig_pg3)
        plt.close()

    # 5. ENVIAR ARCHIVO
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"reporte_ventas_{datetime.now().date()}.pdf", mimetype='application/pdf')

if __name__ == '__main__':
    if collection.count_documents({}) == 0:
        cargar_datos_desde_json()
    app.run(host='0.0.0.0', port=5000, debug=True)