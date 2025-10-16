"""
Servicio para buscar archivos en Google Drive
Permite buscar en múltiples carpetas específicas de Diego Fares
"""

import os
import json
import logging
from typing import List, Optional, Dict, Any
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Configurar logging
logger = logging.getLogger(__name__)

# Scopes necesarios para Google Drive API
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']


class SourceLinker:
    """Clase para mapear nombres de archivos a títulos legibles"""
    def __init__(self, json_path: str = "fuente_agente_v1.json"):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                self.reference_data = json.load(f)

            # Crear diccionarios de lookup (con y sin extensión)
            self.file_to_title = {}
            self.file_to_link = {}
            self.file_no_ext_to_title = {}  # Para matching sin extensión

            for item in self.reference_data:
                if isinstance(item, dict) and "file" in item and "title" in item:
                    file_name = item["file"]
                    title = item["title"]

                    # Guardar con nombre completo
                    self.file_to_title[file_name] = title
                    if "link" in item:
                        self.file_to_link[file_name] = item["link"]

                    # Guardar también sin extensión para matching flexible
                    file_no_ext = self._remove_extension(file_name)
                    self.file_no_ext_to_title[file_no_ext] = title

            logger.info(f"SourceLinker loaded {len(self.file_to_title)} file mappings")

        except FileNotFoundError:
            logger.error(f"Reference file not found: {json_path}")
            self.file_to_title = {}
            self.file_to_link = {}
            self.file_no_ext_to_title = {}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            self.file_to_title = {}
            self.file_to_link = {}
            self.file_no_ext_to_title = {}

    def _remove_extension(self, filename: str) -> str:
        """Remover la extensión del archivo"""
        if '.' in filename:
            return filename.rsplit('.', 1)[0]
        return filename

    def get_title(self, filename: str) -> str:
        """Obtener título legible, o devolver el nombre del archivo si no hay match"""
        # DEBUG: Mostrar qué estamos buscando
        print(f"🔍 Buscando: '{filename}'")

        # Primero intentar match exacto
        if filename in self.file_to_title:
            print(f"   ✓ Match exacto encontrado")
            return self.file_to_title[filename]

        # Si no hay match exacto, intentar sin extensión
        filename_no_ext = self._remove_extension(filename)
        print(f"   Intentando sin extensión: '{filename_no_ext}'")

        if filename_no_ext in self.file_no_ext_to_title:
            print(f"   ✓ Match sin extensión encontrado")
            return self.file_no_ext_to_title[filename_no_ext]

        # DEBUG: Mostrar algunas claves similares del diccionario
        print(f"   ✗ NO encontrado. Muestras del JSON:")
        for i, key in enumerate(list(self.file_no_ext_to_title.keys())[:3]):
            print(f"      - '{key}'")

        # Si no hay match, devolver el nombre original
        return filename


class DriveSearchService:
    def __init__(self):
        """Inicializar el servicio de Google Drive"""
        self.service = self._get_drive_service()
        self.source_linker = SourceLinker()

        # Configuración de carpetas desde variables de entorno
        self.carpetas = {
            'todas': os.getenv('GOOGLE_DRIVE_PARENT_FOLDER_ID'),
            'articulos': os.getenv('GOOGLE_DRIVE_ARTICULOS_ID'),
            'articulos_revistas': os.getenv('GOOGLE_DRIVE_ARTICULOS_REVISTAS_ID'),
            'audios': os.getenv('GOOGLE_DRIVE_AUDIOS_ID'),
            'contemplaciones': os.getenv('GOOGLE_DRIVE_CONTEMPLACIONES_ID'),
            'libros': os.getenv('GOOGLE_DRIVE_LIBROS_ID'),
            'videos': os.getenv('GOOGLE_DRIVE_VIDEOS_ID')
        }

        # DEBUG: Mostrar valores cargados
        print("DEBUG: Variables de entorno cargadas:")
        for nombre, valor in self.carpetas.items():
            print(f"  {nombre}: {valor}")

    def _get_drive_service(self):
        """Crear y autenticar el servicio de Google Drive usando Service Account"""

        # Primero intentar usar Service Account (para Railway)
        service_account_info = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
        if service_account_info:
            try:
                # Parsear el JSON desde variable de entorno
                service_account_data = json.loads(service_account_info)
                credentials = service_account.Credentials.from_service_account_info(
                    service_account_data, scopes=SCOPES
                )
                print("OK: Usando Service Account desde variable de entorno")
                return build('drive', 'v3', credentials=credentials)
            except Exception as e:
                print(f"ERROR: Error con Service Account: {e}")

        # Fallback: buscar archivo service-account.json local
        if os.path.exists('service-account.json'):
            try:
                credentials = service_account.Credentials.from_service_account_file(
                    'service-account.json', scopes=SCOPES
                )
                print("OK: Usando Service Account desde archivo local")
                return build('drive', 'v3', credentials=credentials)
            except Exception as e:
                print(f"ERROR: Error con archivo Service Account: {e}")

        # Último fallback: OAuth (solo para desarrollo local)
        print("WARNING: Service Account no encontrado, usando OAuth (solo desarrollo local)")
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists('credentials.json'):
                    raise Exception("ERROR: No se encontro credentials.json para OAuth")

                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=8080, open_browser=True)

            with open('token.json', 'w') as token:
                token.write(creds.to_json())

        return build('drive', 'v3', credentials=creds)

    def buscar_en_carpeta(self, query: str, carpeta_id: str) -> List[Dict]:
        """Buscar archivos en una carpeta específica usando full-text search y paginación."""
        archivos_formateados = []
        page_token = None
        try:
            while True:
                # Construir query de búsqueda para full-text search
                search_query = f"fullText contains '{query}' and '{carpeta_id}' in parents"

                print(f"DEBUG: Buscando en carpeta {carpeta_id} con query: {search_query}")

                # Ejecutar búsqueda
                results = self.service.files().list(
                    q=search_query,
                    pageSize=1000,  # Aumentado para eficiencia
                    fields="nextPageToken, files(id, name, webViewLink, webContentLink, mimeType, size, modifiedTime)",
                    pageToken=page_token
                ).execute()

                archivos = results.get('files', [])
                print(f"DEBUG: Encontrados {len(archivos)} archivos en esta página.")

                # Formatear y agregar resultados
                for archivo in archivos:
                    file_name = archivo.get('name')
                    # Buscar título legible desde fuente_agente_v1.json
                    display_title = self.source_linker.get_title(file_name)

                    # DEBUG: Log para ver si encuentra el título
                    if display_title != file_name:
                        print(f"✓ MATCH: '{file_name}' -> '{display_title}'")
                    else:
                        print(f"✗ NO MATCH: '{file_name}' (sin título en JSON)")

                    archivos_formateados.append({
                        "id": archivo.get('id'),
                        "name": display_title,  # Usar título legible en lugar del nombre del archivo
                        "file_name": file_name,  # Mantener el nombre original del archivo para referencia
                        "view_link": archivo.get('webViewLink'),
                        "download_link": f"https://drive.google.com/file/d/{archivo.get('id')}/view",
                        "mime_type": archivo.get('mimeType'),
                        "size": archivo.get('size'),
                        "modified_time": archivo.get('modifiedTime')
                    })

                # Avanzar a la siguiente página
                page_token = results.get('nextPageToken', None)
                if page_token is None:
                    break  # Salir del bucle si no hay más páginas

            print(f"DEBUG: Total de archivos encontrados: {len(archivos_formateados)}")
            return archivos_formateados

        except HttpError as error:
            print(f"Error en Google Drive API: {error}")
            return []
        except Exception as error:
            print(f"Error inesperado: {error}")
            return []

    def buscar_en_todas_las_carpetas(self, query: str) -> Dict[str, List[Dict]]:
        """Buscar en todas las carpetas configuradas. Si la query está vacía, devuelve todos los archivos."""
        resultados = {}

        for nombre_carpeta, carpeta_id in self.carpetas.items():
            if carpeta_id and nombre_carpeta != 'todas':  # Omitir la carpeta padre
                print(f"Procesando carpeta: {nombre_carpeta}...")
                
                # Si la query está vacía, obtener todos los archivos de la carpeta
                if not query or not query.strip():
                    print(f"No hay query, listando todos los archivos en {nombre_carpeta}...")
                    archivos = self.obtener_archivos_de_carpeta(carpeta_id)
                else:
                    # Si hay query, buscar normalmente
                    print(f"Buscando '{query}' en {nombre_carpeta}...")
                    archivos = self.buscar_en_carpeta(query, carpeta_id)
                
                if archivos:
                    resultados[nombre_carpeta] = archivos

        return resultados

    def listar_carpetas_disponibles(self) -> Dict[str, str]:
        """Devolver las carpetas configuradas"""
        print(f"DEBUG: Carpetas cargadas: {self.carpetas}")
        return {k: v for k, v in self.carpetas.items() if v and v != 'None'}

    def obtener_archivos_de_carpeta(self, carpeta_id: str) -> List[Dict]:
        """Obtener todos los archivos de una carpeta específica usando paginación."""
        archivos_formateados = []
        page_token = None
        try:
            while True:
                query = f"'{carpeta_id}' in parents"

                results = self.service.files().list(
                    q=query,
                    pageSize=100, # Aumentado para eficiencia
                    fields="nextPageToken, files(id, name, webViewLink, webContentLink, mimeType, size, modifiedTime)",
                    pageToken=page_token
                ).execute()

                archivos = results.get('files', [])

                for archivo in archivos:
                    file_name = archivo.get('name')
                    # Buscar título legible desde fuente_agente_v1.json
                    display_title = self.source_linker.get_title(file_name)

                    # DEBUG: Log para ver si encuentra el título
                    if display_title != file_name:
                        print(f"✓ MATCH: '{file_name}' -> '{display_title}'")
                    else:
                        print(f"✗ NO MATCH: '{file_name}' (sin título en JSON)")

                    archivos_formateados.append({
                        "id": archivo.get('id'),
                        "name": display_title,  # Usar título legible en lugar del nombre del archivo
                        "file_name": file_name,  # Mantener el nombre original del archivo para referencia
                        "view_link": archivo.get('webViewLink'),
                        "download_link": f"https://drive.google.com/file/d/{archivo.get('id')}/view",
                        "mime_type": archivo.get('mimeType'),
                        "size": archivo.get('size'),
                        "modified_time": archivo.get('modifiedTime')
                    })
                
                page_token = results.get('nextPageToken', None)
                if page_token is None:
                    break

            return archivos_formateados

        except Exception as error:
            print(f"Error obteniendo archivos: {error}")
            return []


# Instancia global del servicio
drive_service = DriveSearchService()