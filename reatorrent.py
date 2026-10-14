"""ReaTorrent: una app sencilla para descargar torrents.

Usa libtorrent para la parte de los torrents y tkinter para la ventana.
La búsqueda usa Internet Archive (archive.org), que tiene contenido legal.
"""
import json
import os
import queue
import sys
import threading
import time
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import libtorrent as lt

NOMBRE = "ReaTorrent"
# Carpeta de la app: junto al .exe si es la versión portable, o junto a este archivo.
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(BASE, "datos")
REANUDAR = os.path.join(DATOS, "reanudar")
BUSQUEDAS = os.path.join(DATOS, "torrents-buscados")
CONFIG = os.path.join(DATOS, "config.json")
CONFIG_INICIAL = {
    "carpeta": "Descargas",  # relativa a BASE para que la app sea portable
    "limite_bajada": 0,  # KB/s, 0 = sin límite
    "limite_subida": 0,
}
ESTADOS = {
    "checking_files": "Revisando",
    "checking_resume_data": "Revisando",
    "downloading_metadata": "Buscando datos",
    "downloading": "Bajando",
    "finished": "Terminado",
    "seeding": "Compartiendo",
    "allocating": "Preparando",
}
AGENTE = f"{NOMBRE}/1.0"
ARCHIVE_BUSCAR = "https://archive.org/advancedsearch.php"
ARCHIVE_TORRENT = "https://archive.org/download/{id}/{id}_archive.torrent"


# ---------- Utilidades ----------

def tam(n):
    n = float(n or 0)
    for unidad in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unidad == "TB":
            return f"{n:.0f} {unidad}" if unidad == "B" else f"{n:.1f} {unidad}"
        n /= 1024


def vel(n):
    return f"{tam(n)}/s" if n > 0 else "—"


def tiempo(seg):
    if seg is None or seg <= 0:
        return "—"
    seg = int(seg)
    if seg >= 86400:
        return f"{seg // 86400} d {seg % 86400 // 3600} h"
    if seg >= 3600:
        return f"{seg // 3600} h {seg % 3600 // 60} min"
    if seg >= 60:
        return f"{seg // 60} min {seg % 60} s"
    return f"{seg} s"


def clave(h):
    """Identificador único de un torrent (su hash)."""
    return str(h.info_hashes().get_best())


def nombre_estado(s):
    estado = getattr(s.state, "name", str(s.state))
    return ESTADOS.get(estado, estado)


def esta_pausado(s):
    return bool(s.flags & lt.torrent_flags.paused)


def a_relativa(ruta):
    """Si la ruta está dentro de la carpeta de la app, la guarda relativa (portable)."""
    if not os.path.isabs(ruta):
        return ruta
    try:
        rel = os.path.relpath(ruta, BASE)
    except ValueError:  # está en otro disco
        return ruta
    return ruta if rel.startswith("..") else rel


def a_absoluta(ruta):
    return os.path.normpath(os.path.join(BASE, ruta))


def leer_config():
    cfg = dict(CONFIG_INICIAL)
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    cfg["carpeta"] = a_relativa(cfg["carpeta"])
    return cfg


def guardar_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ---------- La app ----------

class App:
    def __init__(self, raiz):
        for carpeta in (DATOS, REANUDAR, BUSQUEDAS):
            os.makedirs(carpeta, exist_ok=True)
        self.cfg = leer_config()
        os.makedirs(self.carpeta(), exist_ok=True)

        self.ses = lt.session({
            "listen_interfaces": "0.0.0.0:6881,[::]:6881",
            "user_agent": f"{AGENTE} libtorrent/{lt.__version__}",
            "enable_dht": True,
            "alert_mask": lt.alert_category.error | lt.alert_category.status
            | lt.alert_category.storage,
        })
        self.aplicar_limites()

        self.raiz = raiz
        self.cola = queue.Queue()  # mensajes de los hilos de búsqueda
        self.ultimo_guardado = time.time()
        self.cerrando = False

        raiz.title(NOMBRE)
        try:
            raiz.iconbitmap(default=os.path.join(BASE, "reatorrent.ico"))
        except tk.TclError:
            pass
        raiz.geometry("1100x680")
        raiz.minsize(800, 500)
        raiz.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.crear_ventana()
        self.cargar_reanudar()

        for arg in sys.argv[1:]:
            self.agregar_texto(arg)

        self.ciclo()

    # ----- Ventana -----

    def crear_ventana(self):
        estilo = ttk.Style()
        if "vista" in estilo.theme_names():
            estilo.theme_use("vista")
        estilo.configure("Treeview", rowheight=24)

        barra = ttk.Frame(self.raiz, padding=(6, 6, 6, 0))
        barra.pack(fill="x")
        botones = [
            ("Abrir .torrent", self.abrir_archivo),
            ("Pegar magnet", self.pegar_magnet),
            ("Pausar", self.pausar),
            ("Reanudar", self.reanudar),
            ("Quitar", self.quitar),
            ("Límites de velocidad", self.ventana_limites),
            ("Carpeta de descargas", self.elegir_carpeta),
            ("Abrir carpeta", lambda: os.startfile(self.carpeta())),
        ]
        for texto, accion in botones:
            ttk.Button(barra, text=texto, command=accion).pack(side="left", padx=2)

        self.pestanas = ttk.Notebook(self.raiz)
        self.pestanas.pack(fill="both", expand=True, padx=6, pady=6)
        self.crear_pestana_descargas()
        self.crear_pestana_buscar()

        self.barra_estado = ttk.Label(self.raiz, anchor="w", padding=(8, 2))
        self.barra_estado.pack(fill="x", side="bottom")

    def tabla(self, padre, columnas, alto=10):
        """Crea una tabla con barra de desplazamiento. columnas = [(id, título, ancho)]."""
        marco = ttk.Frame(padre)
        arbol = ttk.Treeview(marco, columns=[c[0] for c in columnas],
                             show="headings", height=alto)
        for cid, titulo, ancho in columnas:
            arbol.heading(cid, text=titulo)
            arbol.column(cid, width=ancho, anchor="w" if ancho > 150 else "center",
                         stretch=ancho > 150)
        barra = ttk.Scrollbar(marco, orient="vertical", command=arbol.yview)
        arbol.configure(yscrollcommand=barra.set)
        arbol.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")
        return marco, arbol

    def crear_pestana_descargas(self):
        pestana = ttk.Frame(self.pestanas)
        self.pestanas.add(pestana, text="  Descargas  ")
        division = ttk.PanedWindow(pestana, orient="vertical")
        division.pack(fill="both", expand=True)

        marco, self.lista = self.tabla(division, [
            ("nombre", "Nombre", 360), ("tamano", "Tamaño", 90),
            ("avance", "Avance", 80), ("estado", "Estado", 110),
            ("bajada", "Bajada", 95), ("subida", "Subida", 95),
            ("personas", "Personas", 75), ("falta", "Falta", 100),
        ], alto=12)
        self.lista.bind("<<TreeviewSelect>>", lambda e: self.actualizar_detalles(nuevo=True))
        division.add(marco, weight=3)

        detalles = ttk.Notebook(division)
        division.add(detalles, weight=2)

        # General
        general = ttk.Frame(detalles, padding=10)
        detalles.add(general, text="General")
        self.info = {}
        campos = ["Nombre", "Guardado en", "Tamaño", "Bajado", "Subido",
                  "Proporción", "Semillas", "Personas", "Hash"]
        for fila, campo in enumerate(campos):
            ttk.Label(general, text=campo + ":", font=("Segoe UI", 9, "bold")) \
                .grid(row=fila, column=0, sticky="w", padx=(0, 10), pady=1)
            etiqueta = ttk.Label(general, text="—")
            etiqueta.grid(row=fila, column=1, sticky="w", pady=1)
            self.info[campo] = etiqueta

        # Archivos
        archivos = ttk.Frame(detalles)
        detalles.add(archivos, text="Archivos")
        ttk.Label(archivos, text="Doble clic en un archivo para bajarlo u omitirlo.",
                  padding=(4, 4)).pack(anchor="w")
        marco, self.archivos = self.tabla(archivos, [
            ("ruta", "Archivo", 450), ("tamano", "Tamaño", 90),
            ("avance", "Avance", 80), ("bajar", "¿Bajar?", 70),
        ], alto=6)
        marco.pack(fill="both", expand=True)
        self.archivos.bind("<Double-1>", self.cambiar_prioridad)

        # Personas
        personas = ttk.Frame(detalles)
        detalles.add(personas, text="Personas")
        marco, self.personas = self.tabla(personas, [
            ("ip", "Dirección", 200), ("cliente", "Programa", 160),
            ("avance", "Tiene", 70), ("bajada", "Bajada", 95), ("subida", "Subida", 95),
        ], alto=6)
        marco.pack(fill="both", expand=True)

    def crear_pestana_buscar(self):
        pestana = ttk.Frame(self.pestanas, padding=8)
        self.pestanas.add(pestana, text="  Buscar  ")

        arriba = ttk.Frame(pestana)
        arriba.pack(fill="x")
        ttk.Label(arriba, text="Buscar en Internet Archive (contenido legal):") \
            .pack(side="left")
        self.texto_buscar = ttk.Entry(arriba, width=45)
        self.texto_buscar.pack(side="left", padx=6)
        self.texto_buscar.bind("<Return>", lambda e: self.buscar())
        self.tipo_buscar = ttk.Combobox(arriba, state="readonly", width=14, values=[
            "Todo", "Películas", "Música", "Libros", "Programas"])
        self.tipo_buscar.current(0)
        self.tipo_buscar.pack(side="left", padx=4)
        ttk.Button(arriba, text="Buscar", command=self.buscar).pack(side="left", padx=4)

        marco, self.resultados = self.tabla(pestana, [
            ("titulo", "Título", 480), ("tipo", "Tipo", 90),
            ("tamano", "Tamaño", 90), ("descargas", "Veces bajado", 100),
        ], alto=15)
        marco.pack(fill="both", expand=True, pady=8)
        self.resultados.bind("<Double-1>", lambda e: self.bajar_resultado())

        abajo = ttk.Frame(pestana)
        abajo.pack(fill="x")
        ttk.Button(abajo, text="Bajar el seleccionado",
                   command=self.bajar_resultado).pack(side="left")
        self.aviso_buscar = ttk.Label(abajo, text="")
        self.aviso_buscar.pack(side="left", padx=10)

    def carpeta(self):
        """Carpeta de descargas como ruta completa."""
        return a_absoluta(self.cfg["carpeta"])

    # ----- Agregar torrents -----

    def agregar(self, atp):
        atp.save_path = self.carpeta()
        try:
            h = self.ses.add_torrent(atp)
        except RuntimeError as e:
            messagebox.showwarning(NOMBRE, f"No se pudo agregar el torrent.\n\n{e}")
            return None
        h.save_resume_data(lt.torrent_handle.save_info_dict)
        self.pestanas.select(0)
        return h

    def agregar_archivo(self, ruta):
        try:
            atp = lt.add_torrent_params()
            atp.ti = lt.torrent_info(ruta)
        except RuntimeError as e:
            messagebox.showerror(NOMBRE, f"Ese archivo no es un torrent válido.\n\n{e}")
            return
        self.agregar(atp)

    def agregar_magnet(self, enlace):
        try:
            atp = lt.parse_magnet_uri(enlace.strip())
        except RuntimeError as e:
            messagebox.showerror(NOMBRE, f"Ese enlace magnet no es válido.\n\n{e}")
            return
        self.agregar(atp)

    def agregar_texto(self, texto):
        if texto.lower().startswith("magnet:"):
            self.agregar_magnet(texto)
        elif os.path.isfile(texto):
            self.agregar_archivo(texto)

    def abrir_archivo(self):
        rutas = filedialog.askopenfilenames(
            title="Abrir torrent", filetypes=[("Torrents", "*.torrent"), ("Todos", "*.*")])
        for ruta in rutas:
            self.agregar_archivo(ruta)

    def pegar_magnet(self):
        try:
            inicial = self.raiz.clipboard_get()
        except tk.TclError:
            inicial = ""
        if not inicial.lower().startswith("magnet:"):
            inicial = ""
        enlace = simpledialog.askstring(NOMBRE, "Pega el enlace magnet:",
                                        initialvalue=inicial, parent=self.raiz)
        if enlace:
            self.agregar_magnet(enlace)

    # ----- Acciones sobre la descarga elegida -----

    def elegidos(self):
        claves = set(self.lista.selection())
        return [h for h in self.ses.get_torrents() if h.is_valid() and clave(h) in claves]

    def elegido(self):
        hs = self.elegidos()
        return hs[0] if hs else None

    def pausar(self):
        for h in self.elegidos():
            h.unset_flags(lt.torrent_flags.auto_managed)
            h.pause()

    def reanudar(self):
        for h in self.elegidos():
            h.set_flags(lt.torrent_flags.auto_managed)
            h.resume()

    def quitar(self):
        hs = self.elegidos()
        if not hs:
            return
        respuesta = messagebox.askyesnocancel(
            NOMBRE, f"¿Quitar {len(hs)} descarga(s) de la lista?\n\n"
                    "Sí = quitar y BORRAR los archivos bajados\n"
                    "No = quitar, pero dejar los archivos\n"
                    "Cancelar = no hacer nada")
        if respuesta is None:
            return
        for h in hs:
            k = clave(h)
            if respuesta:
                self.ses.remove_torrent(h, lt.session.delete_files)
            else:
                self.ses.remove_torrent(h)
            try:
                os.remove(os.path.join(REANUDAR, k + ".fastresume"))
            except OSError:
                pass

    def cambiar_prioridad(self, _evento):
        h = self.elegido()
        fila = self.archivos.focus()
        if not h or not fila:
            return
        i = int(fila)
        actual = h.file_priority(i)
        h.file_priority(i, 0 if actual > 0 else 4)

    # ----- Opciones -----

    def aplicar_limites(self):
        self.ses.apply_settings({
            "download_rate_limit": int(self.cfg["limite_bajada"]) * 1024,
            "upload_rate_limit": int(self.cfg["limite_subida"]) * 1024,
        })

    def ventana_limites(self):
        v = tk.Toplevel(self.raiz)
        v.title("Límites de velocidad")
        v.resizable(False, False)
        v.transient(self.raiz)
        v.grab_set()
        marco = ttk.Frame(v, padding=14)
        marco.pack()
        ttk.Label(marco, text="Escribe 0 para no poner límite.") \
            .grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        valores = {}
        for fila, (campo, texto) in enumerate(
                [("limite_bajada", "Bajada máxima:"), ("limite_subida", "Subida máxima:")], 1):
            ttk.Label(marco, text=texto).grid(row=fila, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=str(self.cfg[campo]))
            ttk.Spinbox(marco, from_=0, to=1000000, increment=50, width=10,
                        textvariable=var).grid(row=fila, column=1, padx=6)
            ttk.Label(marco, text="KB/s").grid(row=fila, column=2, sticky="w")
            valores[campo] = var

        def guardar():
            try:
                nuevos = {c: max(0, int(var.get())) for c, var in valores.items()}
            except ValueError:
                messagebox.showerror(NOMBRE, "Escribe solo números enteros.", parent=v)
                return
            self.cfg.update(nuevos)
            guardar_config(self.cfg)
            self.aplicar_limites()
            v.destroy()

        botones = ttk.Frame(marco)
        botones.grid(row=3, column=0, columnspan=3, pady=(12, 0), sticky="e")
        ttk.Button(botones, text="Guardar", command=guardar).pack(side="left", padx=3)
        ttk.Button(botones, text="Cancelar", command=v.destroy).pack(side="left")

    def elegir_carpeta(self):
        carpeta = filedialog.askdirectory(title="¿Dónde guardar las descargas nuevas?",
                                          initialdir=self.carpeta())
        if carpeta:
            self.cfg["carpeta"] = a_relativa(os.path.normpath(carpeta))
            guardar_config(self.cfg)

    # ----- Búsqueda en Internet Archive -----

    def buscar(self):
        texto = self.texto_buscar.get().strip()
        if not texto:
            return
        tipos = {"Películas": "movies", "Música": "audio",
                 "Libros": "texts", "Programas": "software"}
        tipo = tipos.get(self.tipo_buscar.get())
        consulta = f"({texto})"
        consulta += f" AND mediatype:{tipo}" if tipo else \
            " AND mediatype:(movies OR audio OR texts OR software)"
        self.aviso_buscar.config(text="Buscando…")
        threading.Thread(target=self._hilo_buscar, args=(consulta,), daemon=True).start()

    def _hilo_buscar(self, consulta):
        parametros = [("q", consulta), ("fl[]", "identifier"), ("fl[]", "title"),
                      ("fl[]", "mediatype"), ("fl[]", "item_size"), ("fl[]", "downloads"),
                      ("rows", "100"), ("output", "json")]
        url = ARCHIVE_BUSCAR + "?" + urllib.parse.urlencode(parametros)
        try:
            pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE})
            with urllib.request.urlopen(pedido, timeout=20) as r:
                docs = json.load(r)["response"]["docs"]
            self.cola.put(("resultados", docs))
        except Exception as e:  # noqa: BLE001 - cualquier fallo de red se muestra al usuario
            self.cola.put(("error", f"No se pudo buscar: {e}"))

    def mostrar_resultados(self, docs):
        self.resultados.delete(*self.resultados.get_children())
        tipos = {"movies": "Película", "audio": "Música", "texts": "Libro", "software": "Programa"}
        for d in docs:
            self.resultados.insert("", "end", iid=d["identifier"], values=(
                d.get("title", d["identifier"]),
                tipos.get(d.get("mediatype"), d.get("mediatype", "")),
                tam(d["item_size"]) if d.get("item_size") else "—",
                f"{int(d.get('downloads') or 0):,}".replace(",", "."),
            ))
        self.aviso_buscar.config(text=f"{len(docs)} resultado(s). Doble clic para bajar.")

    def bajar_resultado(self):
        ident = self.resultados.focus()
        if not ident:
            return
        self.aviso_buscar.config(text="Pidiendo el torrent…")
        threading.Thread(target=self._hilo_bajar_torrent, args=(ident,), daemon=True).start()

    def _hilo_bajar_torrent(self, ident):
        url = ARCHIVE_TORRENT.format(id=urllib.parse.quote(ident))
        ruta = os.path.join(BUSQUEDAS, "".join(c for c in ident if c.isalnum() or c in "-_.")
                            + ".torrent")
        try:
            pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE})
            with urllib.request.urlopen(pedido, timeout=30) as r, open(ruta, "wb") as f:
                f.write(r.read())
            self.cola.put(("torrent", ruta))
        except Exception as e:  # noqa: BLE001
            self.cola.put(("error", f"No se pudo bajar el torrent: {e}"))

    def revisar_cola(self):
        while True:
            try:
                mensaje = self.cola.get_nowait()
            except queue.Empty:
                return
            tipo, dato = mensaje
            if tipo == "resultados":
                self.mostrar_resultados(dato)
            elif tipo == "torrent":
                self.aviso_buscar.config(text="Agregado a Descargas.")
                self.agregar_archivo(dato)
            elif tipo == "error":
                self.aviso_buscar.config(text=dato)

    # ----- Guardar y cargar el avance -----

    def guardar_reanudar(self, alerta):
        try:
            params = alerta.params
            params.save_path = a_relativa(params.save_path)
            datos = lt.write_resume_data_buf(params)
            with open(os.path.join(REANUDAR, clave(alerta.handle) + ".fastresume"), "wb") as f:
                f.write(datos)
        except (OSError, RuntimeError):
            pass

    def cargar_reanudar(self):
        for nombre in os.listdir(REANUDAR):
            if not nombre.endswith(".fastresume"):
                continue
            try:
                with open(os.path.join(REANUDAR, nombre), "rb") as f:
                    atp = lt.read_resume_data(f.read())
                atp.save_path = a_absoluta(atp.save_path)
                self.ses.add_torrent(atp)
            except (OSError, RuntimeError):
                continue

    def procesar_alertas(self):
        pendientes = 0
        for a in self.ses.pop_alerts():
            if isinstance(a, lt.save_resume_data_alert):
                self.guardar_reanudar(a)
                pendientes += 1
            elif isinstance(a, lt.save_resume_data_failed_alert):
                pendientes += 1
            elif isinstance(a, (lt.torrent_error_alert, lt.file_error_alert)):
                self.barra_estado.config(text="Aviso: " + a.message())
        return pendientes

    def pedir_guardado(self):
        n = 0
        for h in self.ses.get_torrents():
            if h.is_valid() and h.need_save_resume_data():
                h.save_resume_data(lt.torrent_handle.save_info_dict)
                n += 1
        return n

    # ----- Actualización cada segundo -----

    def ciclo(self):
        if self.cerrando:
            return
        self.procesar_alertas()
        self.revisar_cola()
        self.actualizar_lista()
        self.actualizar_detalles()
        if time.time() - self.ultimo_guardado > 60:
            self.pedir_guardado()
            self.ultimo_guardado = time.time()
        self.raiz.after(1000, self.ciclo)

    def actualizar_lista(self):
        vistos = set()
        total_baja = total_sube = 0
        for h in self.ses.get_torrents():
            if not h.is_valid():
                continue
            s = h.status()
            k = clave(h)
            vistos.add(k)
            total_baja += s.download_rate
            total_sube += s.upload_rate
            pausado = esta_pausado(s)
            falta = s.total_wanted - s.total_wanted_done
            valores = (
                s.name or "(buscando nombre…)",
                tam(s.total_wanted) if s.has_metadata else "—",
                f"{s.progress * 100:.1f} %",
                "En pausa" if pausado else nombre_estado(s),
                "—" if pausado else vel(s.download_rate),
                "—" if pausado else vel(s.upload_rate),
                f"{s.num_seeds}/{s.num_peers}",
                tiempo(falta / s.download_rate) if s.download_rate and not pausado else "—",
            )
            if self.lista.exists(k):
                self.lista.item(k, values=valores)
            else:
                self.lista.insert("", "end", iid=k, values=valores)
        for k in self.lista.get_children():
            if k not in vistos:
                self.lista.delete(k)

        limites = []
        if self.cfg["limite_bajada"]:
            limites.append(f"bajada máx. {self.cfg['limite_bajada']} KB/s")
        if self.cfg["limite_subida"]:
            limites.append(f"subida máx. {self.cfg['limite_subida']} KB/s")
        texto = f"Bajando: {vel(total_baja)}    Subiendo: {vel(total_sube)}"
        texto += f"    Límites: {', '.join(limites) if limites else 'ninguno'}"
        texto += f"    Carpeta: {self.carpeta()}"
        if not self.barra_estado.cget("text").startswith("Aviso"):
            self.barra_estado.config(text=texto)

    def actualizar_detalles(self, nuevo=False):
        h = self.elegido()
        if nuevo:
            self.archivos.delete(*self.archivos.get_children())
            self.barra_estado.config(text="")
        if not h:
            for etiqueta in self.info.values():
                etiqueta.config(text="—")
            self.personas.delete(*self.personas.get_children())
            return
        s = h.status()
        proporcion = s.all_time_upload / s.all_time_download if s.all_time_download else 0
        datos = {
            "Nombre": s.name or "—",
            "Guardado en": s.save_path,
            "Tamaño": tam(s.total_wanted) if s.has_metadata else "—",
            "Bajado": tam(s.all_time_download),
            "Subido": tam(s.all_time_upload),
            "Proporción": f"{proporcion:.2f}",
            "Semillas": f"{s.num_seeds} conectadas de {max(s.list_seeds, 0)} conocidas",
            "Personas": f"{s.num_peers} conectadas de {max(s.list_peers, 0)} conocidas",
            "Hash": clave(h),
        }
        for campo, valor in datos.items():
            self.info[campo].config(text=valor)

        # Archivos
        ti = h.torrent_file()
        if ti is not None:
            fs = ti.files()
            avance = h.file_progress()
            prioridades = h.get_file_priorities()
            for i in range(fs.num_files()):
                tamano = fs.file_size(i)
                valores = (
                    fs.file_path(i), tam(tamano),
                    f"{(avance[i] / tamano * 100) if tamano else 100:.1f} %",
                    "Sí" if prioridades[i] > 0 else "No",
                )
                if self.archivos.exists(str(i)):
                    self.archivos.item(str(i), values=valores)
                else:
                    self.archivos.insert("", "end", iid=str(i), values=valores)

        # Personas
        self.personas.delete(*self.personas.get_children())
        for p in h.get_peer_info():
            cliente = p.client.decode("utf-8", "replace") if isinstance(p.client, bytes) \
                else str(p.client)
            self.personas.insert("", "end", values=(
                f"{p.ip[0]}:{p.ip[1]}", cliente or "?", f"{p.progress * 100:.0f} %",
                vel(p.down_speed), vel(p.up_speed),
            ))

    # ----- Cerrar -----

    def cerrar(self):
        self.cerrando = True
        self.barra_estado.config(text="Guardando el avance…")
        self.raiz.update_idletasks()
        self.procesar_alertas()
        pendientes = self.pedir_guardado()
        limite = time.time() + 10
        while pendientes > 0 and time.time() < limite:
            self.ses.wait_for_alert(500)
            pendientes -= self.procesar_alertas()
        self.ses.pause()
        self.raiz.destroy()


def main():
    try:  # para que la barra de tareas muestre el ícono de la app y no el de Python
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ReaTorrent.App")
    except (AttributeError, OSError):
        pass
    raiz = tk.Tk()
    App(raiz)
    raiz.mainloop()


if __name__ == "__main__":
    main()
