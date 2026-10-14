<p align="center">
  <img src="reatorrent.png" width="96" alt="Ícono de ReaTorrent">
</p>

<h1 align="center">ReaTorrent</h1>

<p align="center">
  Una app sencilla para Windows para descargar torrents.<br>
  Portable, en español y sin instalación.
</p>

---

## ¿Qué es?

ReaTorrent es un programa para bajar archivos por BitTorrent. Tiene una ventana simple con todo lo
necesario: agregar torrents, ver su avance, controlar la velocidad y buscar contenido legal en
[Internet Archive](https://archive.org).

Está hecho en Python con [libtorrent](https://www.libtorrent.org/), la misma pieza que usan otros
programas de torrents conocidos, y la ventana usa tkinter, que ya viene con Python.

## Funciones

| Función | Qué hace |
|---|---|
| **Agregar descargas** | Abre archivos `.torrent` o pega enlaces `magnet:` |
| **Lista de descargas** | Muestra el avance, la velocidad, las personas conectadas y el tiempo que falta |
| **Pausar, reanudar y quitar** | Al quitar una descarga puedes elegir si también borras los archivos |
| **Límites de velocidad** | Pon un máximo de bajada y de subida en KB/s (0 = sin límite) |
| **Detalles** | En **General** ves el tamaño, lo bajado, lo subido y la proporción. En **Archivos** eliges cuáles bajar. En **Personas** ves con quién estás conectado |
| **Buscar** | Busca películas, música, libros y programas en Internet Archive y los baja con doble clic |
| **Guarda el avance** | Al cerrar guarda cómo iba cada descarga y al abrir sigue donde iba |
| **Portable** | Todo se guarda en su propia carpeta. Funciona desde una USB o desde cualquier disco |

## Descargar y usar (sin instalar nada)

1. Ve a [**Releases**](../../releases/latest) y baja `ReaTorrent-portable-windows.zip`.
2. Descomprímelo: clic derecho → **Extraer todo**.
3. Abre la carpeta **ReaTorrent** y da doble clic en **ReaTorrent.exe**.

Funciona en Windows 10 y 11 de 64 bits. No separes `ReaTorrent.exe` de la carpeta `_internal`.

## Ejecutar desde el código

Necesitas [Python 3](https://www.python.org/downloads/). Al instalarlo, marca **"Add Python to PATH"**.

```
git clone https://github.com/MarioBertolucci/ReaTorrent.git
cd ReaTorrent
pip install libtorrent
python reatorrent.py
```

### Crear el `.exe` portable

```
pip install pyinstaller
pyinstaller --windowed --onedir --name ReaTorrent --icon reatorrent.ico reatorrent.py
copy reatorrent.ico dist\ReaTorrent\
```

El resultado queda en `dist\ReaTorrent\`.

## Archivos del proyecto

```
reatorrent.py     El código de la app
reatorrent.ico    Ícono (ventana, barra de tareas y .exe)
reatorrent.png    Ícono en imagen grande
LEEME.txt         Instrucciones cortas que van dentro del .zip
```

Cuando la app se usa, crea junto a ella estas carpetas. Git no las sube:

```
datos/            Configuración y avance guardado de cada descarga
Descargas/        Lo que bajas (se puede cambiar desde la app)
```

## Notas

- La búsqueda usa solo **Internet Archive**, que tiene contenido que se puede bajar legalmente.
- Mientras la app está abierta, también comparte (sube) lo que ya bajaste. Así funciona BitTorrent.
- La primera vez, Windows puede preguntar si la app puede usar la red. Hay que permitirlo para que
  pueda conectarse con otras personas.
- Algunos antivirus desconfían de los programas hechos con PyInstaller aunque no tengan nada malo.
