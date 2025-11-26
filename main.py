
"""
API principal que integra servicios de OpenAI y Google Drive
"""

# Solo crear credentials.json si el entorno NO es productivo
import os
if os.environ.get("ENV", "development") != "production":
    if not os.path.exists("credentials.json"):
        creds_env = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if creds_env:
            with open("credentials.json", "w") as f:
                f.write(creds_env)

import logging
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Importar servicios
from openai_service import openai_service
from drive_service import drive_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="OpenAI Assistant Chat API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Modelos Pydantic
class MessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    thread_id: Optional[str] = None


class Citation(BaseModel):
    file_id: str
    file_name: str
    quote: str
    text: str
    download_link: Optional[str] = None


class ChatResponse(BaseModel):
    thread_id: str
    assistant_message: str
    citations: List[Citation]


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Término de búsqueda")
    carpeta: Optional[str] = Field(None, description="Carpeta específica: articulos, audios, libros, etc.")


class DriveSearchResponse(BaseModel):
    success: bool
    total: int
    carpeta_buscada: Optional[str]
    archivos: List[Dict[str, Any]]


# Endpoints de OpenAI Chat
@app.post("/ask", response_model=ChatResponse)
async def ask_openai(req: MessageRequest):
    """
    Chat con el asistente de OpenAI con extracción automática de citas
    """
    try:
        logger.info(f"Processing chat message: {req.message[:50]}...")

        # Usar servicio de OpenAI
        result = openai_service.chat(req.message, req.thread_id)

        # Convertir a formato de respuesta
        citations = [
            Citation(
                file_id=citation.file_id,
                file_name=citation.file_name,
                quote=citation.quote,
                text=citation.text,
                download_link=citation.download_link
            )
            for citation in result.citations
        ]

        return ChatResponse(
            thread_id=result.thread_id,
            assistant_message=result.assistant_message,
            citations=citations
        )

    except Exception as e:
        logger.error(f"Error in chat: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# Endpoints de Google Drive
@app.post("/search-drive", response_model=DriveSearchResponse)
async def search_drive(req: SearchRequest):
    """
    Buscar archivos en Google Drive

    - **query**: Término de búsqueda
    - **carpeta**: (Opcional) Carpeta específica: articulos, audios, libros, etc.
    """
    try:
        logger.info(f"Buscando '{req.query}' en Drive")

        if req.carpeta:
            # Buscar en carpeta específica
            carpeta_id = drive_service.carpetas.get(req.carpeta)
            if not carpeta_id or carpeta_id == 'None':
                raise HTTPException(
                    status_code=400,
                    detail=f"Carpeta '{req.carpeta}' no configurada en el servidor"
                )

            archivos = drive_service.buscar_en_carpeta(req.query, carpeta_id)

            return DriveSearchResponse(
                success=True,
                total=len(archivos),
                carpeta_buscada=req.carpeta,
                archivos=archivos
            )

        else:
            # Buscar en todas las carpetas
            resultados = drive_service.buscar_en_todas_las_carpetas(req.query)

            # Aplanar resultados
            todos_los_archivos = []
            for carpeta, archivos in resultados.items():
                for archivo in archivos:
                    archivo['carpeta_origen'] = carpeta
                    todos_los_archivos.append(archivo)

            return DriveSearchResponse(
                success=True,
                total=len(todos_los_archivos),
                carpeta_buscada=None,
                archivos=todos_los_archivos
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error buscando en Drive: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


# Endpoint de salud
@app.get("/health")
async def health_check():
    """Verificar estado de los servicios"""
    return {
        "status": "healthy",
        "openai_enabled": True,
        "drive_enabled": True,
        "assistant_id": openai_service.assistant_id,
        "references_loaded": len(openai_service.source_linker.reference_data)
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)