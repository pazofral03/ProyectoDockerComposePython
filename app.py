import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io
import base64
import os
import json
import subprocess
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
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

# --- FUNCIONES AUXILIARES ---

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
    ventas = list(collection.find())
    if not ventas:
        return render_template('dashboard.html', plot_url=None, kpis=None)

    kpis = obtener_kpis(ventas)
    
    # Parche de fechas
    for v in ventas:
        if 'fecha' not in v: v['fecha'] = datetime.now()
    ventas_por_fecha = sorted(ventas, key=lambda x: x['fecha'])

    # --- CONFIGURACIÓN VISUAL ---
    plt.style.use('ggplot')
    fig, axs = plt.subplots(2, 2, figsize=(16, 12), facecolor='white')

    # ==============================================================================
    # 1. MAPEO DE COLORES CONSISTENTE (SOLUCIÓN A TU PROBLEMA)
    # ==============================================================================
    # Obtenemos lista única de todos los productos en la base de datos
    todos_productos = sorted(list(set(v['producto'] for v in ventas)))
    
    # Generamos una paleta de colores fija
    # 'tab20' tiene 20 colores distintos. Si tienes más productos, se repetirán ciclo.
    paleta = plt.cm.tab20(range(len(todos_productos)))
    
    # Creamos el diccionario: { "Monitor": (0.1, 0.2, 0.5), "Ratón": ... }
    product_colors = {prod: paleta[i % 20] for i, prod in enumerate(todos_productos)}
    
    # Añadimos manualmente el color para la categoría "Otros" (Gris claro)
    product_colors['Otros'] = '#d3d3d3' 
    # ==============================================================================

    # --- GRÁFICO 1: BARRAS (Unidades) ---
    prod_agrupado = {}
    for v in ventas:
        prod_agrupado[v['producto']] = prod_agrupado.get(v['producto'], 0) + v['cantidad']
    
    lista_prods_barras = list(prod_agrupado.keys())
    lista_vals_barras = list(prod_agrupado.values())
    
    # ASIGNAMOS COLORES: Buscamos el color de cada producto en el diccionario
    colores_barras = [product_colors.get(p, '#333333') for p in lista_prods_barras]
    
    ax1 = axs[0, 0]
    ax1.bar(lista_prods_barras, lista_vals_barras, color=colores_barras)
    ax1.set_title('Total Unidades Vendidas', fontsize=12, weight='bold')
    ax1.tick_params(axis='x', rotation=45, labelsize=9)

    # --- GRÁFICO 2: TARTA (Ingresos Top 5 + Otros) ---
    labels_pie, values_pie = preparar_datos_tarta(ventas)
    
    # ASIGNAMOS COLORES: Buscamos en el diccionario (incluyendo 'Otros')
    colores_tarta = [product_colors.get(label, '#d3d3d3') for label in labels_pie]
    
    ax2 = axs[0, 1]
    explode = [0.1] + [0]*(len(values_pie)-1) if values_pie else None
    ax2.pie(values_pie, labels=labels_pie, autopct='%1.1f%%', startangle=140, 
            explode=explode, shadow=True, colors=colores_tarta) # Usamos la lista personalizada
    ax2.set_title('Ingresos (Top 5 + Otros)', fontsize=12, weight='bold')

    # --- GRÁFICO 3: PARETO (Ingresos) ---
    ingresos_por_prod = {}
    for v in ventas:
        ingresos_por_prod[v['producto']] = ingresos_por_prod.get(v['producto'], 0) + v['ingresos']
    sorted_pareto = sorted(ingresos_por_prod.items(), key=lambda x: x[1], reverse=True)
    prods_par = [x[0] for x in sorted_pareto]
    ingr_par = [x[1] for x in sorted_pareto]
    total = sum(ingr_par)
    acumulado = [sum(ingr_par[:i+1])/total*100 for i in range(len(ingr_par))]

    # ASIGNAMOS COLORES
    colores_pareto = [product_colors.get(p, '#333333') for p in prods_par]

    ax3 = axs[1, 0]
    ax3.bar(prods_par, ingr_par, color=colores_pareto)
    ax3_twin = ax3.twinx()
    ax3_twin.plot(prods_par, acumulado, color='red', marker='o', linewidth=2)
    ax3_twin.axhline(80, color='gray', linestyle='--')
    ax3.set_title('Pareto de Ingresos', fontsize=12, weight='bold')
    ax3.tick_params(axis='x', rotation=45, labelsize=9)

    # --- GRÁFICO 4: SERIE TEMPORAL ---
    # Este no lleva colores por producto, es temporal global (usamos verde fijo)
    ax4 = axs[1, 1]
    fechas_map = {}
    for v in ventas_por_fecha:
        dia = v['fecha'].date()
        fechas_map[dia] = fechas_map.get(dia, 0) + v['ingresos']
    fechas_ord = sorted(fechas_map.keys())
    valores_tiempo = [fechas_map[d] for d in fechas_ord]
    ax4.plot(fechas_ord, valores_tiempo, marker='o', linestyle='-', color='#2ca02c', linewidth=2)
    ax4.set_title('Evolución de Ingresos', fontsize=12, weight='bold')
    ax4.grid(True, linestyle='--', alpha=0.5)
    ax4.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    ax4.tick_params(axis='x', rotation=45)

    plt.tight_layout()
    img = io.BytesIO()
    plt.savefig(img, format='png', bbox_inches='tight', facecolor=fig.get_facecolor())
    img.seek(0)
    plot_url = base64.b64encode(img.getvalue()).decode()
    plt.close()

    return render_template('dashboard.html', plot_url=plot_url, kpis=kpis)

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
    """Muestra el formulario de edición con los datos cargados."""
    # Buscamos el documento específico por ID
    venta = collection.find_one({'_id': ObjectId(id)})
    return render_template('editar.html', venta=venta)

@app.route('/actualizar/<id>', methods=['POST'])
def actualizar(id):
    """Procesa la actualización en MongoDB."""
    fecha_str = request.form['fecha']
    try:
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d')
    except ValueError:
        fecha_obj = datetime.now()

    # Preparamos los nuevos datos
    datos_actualizados = {
        "producto": request.form['producto'],
        "cantidad": int(request.form['cantidad']),
        "ingresos": float(request.form['ingresos']),
        "fecha": fecha_obj
    }

    # ACTUALIZAMOS en la base de datos ($set reemplaza solo los campos indicados)
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

if __name__ == '__main__':
    if collection.count_documents({}) == 0:
        cargar_datos_desde_json()
    app.run(host='0.0.0.0', port=5000, debug=True)