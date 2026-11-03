import asyncio
import base64
import binascii
import io
import json
import logging
import math
import os
import wave
from enum import Enum
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from meta_sam_parser import (
    InvalidSegmentationMaskError,
    ResponsesStreamError,
    decode_mask_to_svg_path,
    image_segmentation_format,
    parse_responses_stream,
)
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from execution import (
    ExecuteRequest,
    ExecuteResponse,
    ExecutionError,
    action_specs,
    run_execution,
)
from media import media_response, save_generated_image
from documents import (
    DocumentError,
    analyze as analyze_stored_document,
    document_info,
    load_document,
    store_document,
)

# Charge les variables du fichier .env (doit être appelé avant de lire la clé)
load_dotenv(override=True)

raw_key = os.getenv("MODEL_API_KEY")
print("=== DEBUG PYTHON ===")
print("API key configured:", "yes" if raw_key else "no")




print("====================")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reflow")

META_BASE_URL = "https://api.meta.ai/v1"
MODEL_NAME = "muse-spark-1.3"
PLACEHOLDER_KEY = "COLLER_MA_CLE_ICI"
MAX_REQUEST_CHARS = 2000
MAX_PLAN_STEPS = 20
MAX_PLAN_QUESTIONS = 10
MAX_BLOCKS = 4
META_ASR_URL = "https://api.meta.ai/v1/asr/transcribe"
ASR_MODEL_NAME = "muse-voice-transcribe-1.0"
ASR_MODE = "PUSH_TO_TALK"
ASR_AUDIO_ENCODING = "WAV"
ASR_TIMEOUT = 300.0
MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_AUDIO_SECONDS = 600
ASR_SAMPLE_RATES = (16000, 24000)
ASR_MIME_TYPES = ("audio/wav", "audio/x-wav", "audio/vnd.wave")
MAX_CHAT_MESSAGES = 50
MAX_CHAT_HISTORY = 12
CHAT_IMAGE_MIME_TYPES = (
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/x-icon",
)
MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CHAT_IMAGE_B64 = 7000000
IMAGE_MODEL_NAME = "muse-image-1.0"
IMAGE_ALLOWED_SIZES = ("auto", "1792x1024", "1024x1536", "1536x1024")
MAX_IMAGE_PROMPT_CHARS = 2000
MAX_GENERATED_IMAGE_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_B64 = 14 * 1024 * 1024
EDIT_FILENAME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/x-icon": "ico",
}
SAM_MODEL_NAME = "sam-3.1"
SAM_TIMEOUT = 120.0
MAX_SEGMENT_CONCEPT_CHARS = 200
MAX_SEGMENT_MATCHES = 50
MAX_SEGMENT_SVG_CHARS = 200 * 1024
MAX_SEGMENT_TOTAL_SVG_CHARS = 2 * 1024 * 1024
MAX_AGENT_TASKS = 20
DEFAULT_AGENT_TASKS = 10
MAX_TASK_CRITERIA = 8
MAX_MISSING_INFO = 10
MAX_AGENT_QUESTIONS = 5
CHAT_IMAGE_ONLY_TEXT = "Describe this image precisely, then analyze its content."
CHAT_IMAGE_INSTRUCTIONS = (
    "One message contains an image: examine it carefully before replying. "
    "Understand the exact intent of the question and answer it directly, "
    "with no unnecessary introduction. Identify the visible objects, their "
    "features and the important details. Read visible text when it is "
    "legible enough and quote it. Clearly distinguish certain observations "
    "from your interpretations. Flag blurry, partial or unidentifiable "
    "elements without ever inventing information. Match the length and "
    "level of detail to the question asked. Reply in English."
)


class PlanRequest(BaseModel):
    """Demande libre à transformer en plan d'action structuré."""

    request: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)


class Block(BaseModel):
    """Bloc de présentation : un format d'affichage et ses données."""

    type: str = Field(
        pattern="^(checklist|table|tree|timeline|decision|procedure|schedule|summary|article)$"
    )
    title: str = Field(default="", max_length=200)
    content: dict = Field(default_factory=dict)


class PlanResponse(BaseModel):
    """Plan d'action structuré proposé par le modèle."""

    title: str = Field(min_length=1, max_length=200)
    steps: list[str] = Field(min_length=1, max_length=MAX_PLAN_STEPS)
    questions: list[str] = Field(default_factory=list, max_length=MAX_PLAN_QUESTIONS)
    blocks: list[Block] = Field(default_factory=list)


class TranscribeResponse(BaseModel):
    """Texte transcrit par Meta Voice Transcribe."""

    transcript: str = Field(min_length=1)


class ChatImage(BaseModel):
    """Image jointe à un message utilisateur : MIME + Base64 sans préfixe Data URL."""

    mime: str = Field(min_length=1, max_length=100)
    data: str = Field(min_length=1, max_length=MAX_CHAT_IMAGE_B64)


class ChatMessage(BaseModel):
    """Un tour de parole dans l'historique conversationnel."""

    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(default="", max_length=MAX_REQUEST_CHARS)
    image: Optional[ChatImage] = None


class ChatRequest(BaseModel):
    """Historique envoyé pour obtenir une réponse conversationnelle."""

    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_CHAT_MESSAGES)


class ChatResponse(BaseModel):
    """Réponse naturelle du modèle, sans plan JSON."""

    reply: str = Field(min_length=1)


class ImageRequest(BaseModel):
    """Description textuelle de l'image à générer, taille facultative."""

    prompt: str = Field(min_length=1, max_length=MAX_IMAGE_PROMPT_CHARS)
    size: Optional[str] = None


class ImageResponse(BaseModel):
    """Image générée : MIME détecté + Base64 sans préfixe Data URL."""

    mime: str
    data: str
    media_id: Optional[str] = None


class ImageEditRequest(BaseModel):
    """Instruction de modification + image source à transformer."""

    prompt: str = Field(min_length=1, max_length=MAX_IMAGE_PROMPT_CHARS)
    image: ChatImage


class DocumentUpload(BaseModel):
    """Document importe : nom + MIME declares + contenu Base64 (sans prefixe)."""

    filename: str = Field(min_length=1, max_length=128)
    mime: str = Field(min_length=1, max_length=100)
    data: str = Field(min_length=1, max_length=MAX_DOCUMENT_B64)


class DocumentInfo(BaseModel):
    """Metadonnees publiques d'un document stocke + court apercu."""

    doc_id: str
    filename: str
    format: str
    pages: int
    chars: int
    truncated: bool
    preview: str
    status: str
    message: str


class DocumentAnalyzeRequest(BaseModel):
    """Operation d'analyse sur un document stocke."""

    operation: str = Field(min_length=1, max_length=32)
    question: str = Field(default="", max_length=2000)
    style: str = Field(default="simple", max_length=32)
    language: str = Field(default="en", max_length=8)


class DocumentAnalyzeResponse(BaseModel):
    """Resultat d'analyse : texte, references d'origine, metadonnees."""

    doc_id: str
    operation: str
    result: str
    refs: list[str] = Field(default_factory=list)
    language: str
    chunks_used: int
    found: bool
    model_calls: int
    message: str

class SegmentRequest(BaseModel):
    """Concept à isoler + image source à segmenter (un concept par requête)."""

    concept: str = Field(min_length=1, max_length=MAX_SEGMENT_CONCEPT_CHARS)
    image: ChatImage


class SegmentBox(BaseModel):
    """Boîte en pixels natifs de l'image source (bords droit/bas exclus)."""

    left: float
    top: float
    right: float
    bottom: float


class SegmentMask(BaseModel):
    """Masque SVG dans son raster + placement dans l'image source."""

    width: int
    height: int
    bounds: SegmentBox
    svg: str


class SegmentMatch(BaseModel):
    """Un objet segmenté : boîte obligatoire, masque et score facultatifs."""

    object_id: str
    box: SegmentBox
    confidence: Optional[float] = None
    mask: Optional[SegmentMask] = None


class SegmentImageSize(BaseModel):
    """Dimensions natives de l'image source."""

    width: int
    height: int


class SegmentResponse(BaseModel):
    """Résultat de segmentation : dimensions + objets (liste vide si aucun)."""

    image: SegmentImageSize
    matches: list = []
    truncated: bool = False


class TaskStatus(str, Enum):
    """Statut d'une tâche agentique (statuts initiaux calculés par le serveur)."""

    PENDING = "pending"
    READY = "ready"
    BLOCKED = "blocked"
    DONE = "done"
    SKIPPED = "skipped"


class PlanStatus(str, Enum):
    """Statut global d'un plan agentique."""

    DRAFT = "draft"
    NEEDS_INFO = "needs_info"
    READY = "ready"


class PlanTask(BaseModel):
    """Une tâche du plan : identifiée, ordonnée par dépendances, vérifiable."""

    id: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=1000)
    depends_on: list[str] = Field(default_factory=list, max_length=MAX_AGENT_TASKS)
    success_criteria: list[str] = Field(min_length=1, max_length=MAX_TASK_CRITERIA)
    needs_info: list[str] = Field(default_factory=list, max_length=MAX_MISSING_INFO)
    status: TaskStatus = TaskStatus.PENDING


class MissingInfo(BaseModel):
    """Une information manquante : ce qui manque et les tâches bloquées."""

    id: str = Field(min_length=1, max_length=32)
    field: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=500)
    blocks_task_ids: list[str] = Field(default_factory=list, max_length=MAX_AGENT_TASKS)


class AgentPlanRequest(BaseModel):
    """Demande libre à transformer en plan agentique structuré."""

    request: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)
    max_tasks: int = Field(default=DEFAULT_AGENT_TASKS, ge=1, le=MAX_AGENT_TASKS)


class AgentPlanResponse(BaseModel):
    """Plan agentique : objectif, tâches ordonnées, infos manquantes, statut."""

    goal: str = Field(min_length=1, max_length=500)
    summary: str = Field(default="", max_length=1000)
    tasks: list[PlanTask] = Field(min_length=1, max_length=MAX_AGENT_TASKS)
    missing_information: list[MissingInfo] = Field(
        default_factory=list, max_length=MAX_MISSING_INFO
    )
    questions: list[str] = Field(default_factory=list, max_length=MAX_AGENT_QUESTIONS)
    status: PlanStatus = PlanStatus.DRAFT
    blocks: list[Block] = Field(default_factory=list)


# Création de l'application FastAPI
app = FastAPI(
    title="ReFlow API",
    description="Backend of the ReFlow AI agent",
    version="0.1.1",
)


def get_client() -> OpenAI:
    """Crée le client Meta Model API, ou lève une erreur claire si la clé manque."""
    api_key = os.getenv("MODEL_API_KEY", "").strip()
    if not api_key or api_key == PLACEHOLDER_KEY:
        logger.error("MODEL_API_KEY est absente ou n'a pas été remplacée dans .env")
        raise HTTPException(
            status_code=500,
            detail=(
                "Missing API key. Open the .env file at the project root, "
                "replace COLLER_MA_CLE_ICI with your Meta key, then restart the server."
            ),
        )
    return OpenAI(
        base_url=META_BASE_URL,
        api_key=api_key,
        timeout=60.0,
        max_retries=1,
    )


def get_async_client() -> AsyncOpenAI:
    """Crée le client async Meta Model API (streaming SAM), ou 500 si la clé manque."""
    api_key = os.getenv("MODEL_API_KEY", "").strip()
    if not api_key or api_key == PLACEHOLDER_KEY:
        logger.error("MODEL_API_KEY est absente ou n'a pas été remplacée dans .env")
        raise HTTPException(
            status_code=500,
            detail=(
                "Missing API key. Open the .env file at the project root, "
                "replace COLLER_MA_CLE_ICI with your Meta key, then restart the server."
            ),
        )
    return AsyncOpenAI(
        base_url=META_BASE_URL,
        api_key=api_key,
        timeout=SAM_TIMEOUT,
        max_retries=1,
    )


def api_failure(status_code: int, message: str, error: Exception) -> HTTPException:
    """Journalise l'erreur sans secret (scalaires techniques seuls) et prépare la réponse HTTP."""
    logger.error(
        "Erreur Meta API : %s (statut=%s, code=%s, type=%s, param=%s, req=%s)",
        type(error).__name__,
        getattr(error, "status_code", None),
        getattr(error, "code", None),
        getattr(error, "type", None),
        getattr(error, "param", None),
        getattr(error, "request_id", None),
    )
    return HTTPException(status_code=status_code, detail=message)


def normalize_block_content(block_type, content):
    """Normalise le contenu d'un bloc selon son type. Retourne None si inexploitable."""
    if not isinstance(block_type, str) or not isinstance(content, dict):
        return None

    def clean_text_list(value):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()][:30]

    def clean_object_list(value):
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)][:30]

    def clean_text(value):
        return value.strip() if isinstance(value, str) else ""

    if block_type == "checklist":
        items = clean_text_list(content.get("items"))
        return {"items": items} if items else None
    if block_type == "table":
        headers = clean_text_list(content.get("headers"))[:8]
        rows = []
        raw_rows = content.get("rows")
        if isinstance(raw_rows, list):
            for row in raw_rows[:20]:
                if isinstance(row, dict):
                    row = list(row.values())
                if not isinstance(row, list):
                    continue
                cells = []
                for cell in row[:8]:
                    if isinstance(cell, str):
                        cell = cell.strip()
                    elif isinstance(cell, (int, float)) and not isinstance(cell, bool):
                        cell = str(cell)
                    else:
                        continue
                    if cell:
                        cells.append(cell)
                if cells:
                    rows.append(cells)
        if not headers or not rows:
            return None
        width = len(headers)
        rows = [(row + [""] * width)[:width] for row in rows]
        return {"headers": headers, "rows": rows}
    if block_type == "tree":
        def clean_node(node, depth):
            if isinstance(node, str):
                node = {"label": node}
            if not isinstance(node, dict):
                return None
            label = clean_text(node.get("label"))
            if not label:
                return None
            children = []
            if depth < 4 and isinstance(node.get("children"), list):
                for child in node["children"][:20]:
                    cleaned = clean_node(child, depth + 1)
                    if cleaned is not None:
                        children.append(cleaned)
            result = {"label": label}
            if children:
                result["children"] = children
            return result

        nodes = []
        raw_nodes = content.get("nodes")
        if isinstance(raw_nodes, list):
            for node in raw_nodes[:20]:
                cleaned = clean_node(node, 0)
                if cleaned is not None:
                    nodes.append(cleaned)
        return {"nodes": nodes} if nodes else None
    if block_type == "timeline":
        events = []
        raw_events = content.get("events")
        if isinstance(raw_events, list):
            for event in raw_events[:30]:
                if isinstance(event, str):
                    event = {"label": event}
                if not isinstance(event, dict):
                    continue
                label = clean_text(event.get("label"))
                detail = clean_text(event.get("detail"))
                if label or detail:
                    events.append({"label": label or f"Étape {len(events) + 1}", "detail": detail})
        return {"events": events} if events else None
    if block_type == "decision":
        options = []
        for option in clean_object_list(content.get("options"))[:6]:
            name = clean_text(option.get("name"))
            pros = clean_text_list(option.get("pros"))[:10]
            cons = clean_text_list(option.get("cons"))[:10]
            if not name or (not pros and not cons):
                continue
            options.append({"name": name, "pros": pros, "cons": cons})
        if not options:
            return None
        return {"options": options, "recommendation": clean_text(content.get("recommendation"))}
    if block_type == "procedure":
        steps = []
        raw_steps = content.get("steps")
        if isinstance(raw_steps, list):
            for step in raw_steps[:30]:
                if isinstance(step, str):
                    step = {"title": step}
                if not isinstance(step, dict):
                    continue
                title = clean_text(step.get("title"))
                if not title:
                    continue
                steps.append({"title": title, "detail": clean_text(step.get("detail"))})
        return {"steps": steps} if steps else None
    if block_type == "schedule":
        days = []
        for day in clean_object_list(content.get("days"))[:14]:
            label = clean_text(day.get("label"))
            if not label:
                continue
            days.append({"label": label, "tasks": clean_text_list(day.get("tasks"))[:20]})
        return {"days": days} if days else None
    if block_type == "summary":
        points = clean_text_list(content.get("points"))
        conclusion = clean_text(content.get("conclusion"))
        if not points and not conclusion:
            return None
        return {"points": points, "conclusion": conclusion}
    if block_type == "article":
        introduction = clean_text(content.get("introduction"))
        sections = []
        for section in clean_object_list(content.get("sections"))[:20]:
            heading = clean_text(section.get("heading"))
            paragraphs = clean_text_list(section.get("paragraphs"))[:30]
            if not heading or not paragraphs:
                continue
            sections.append({"heading": heading, "paragraphs": paragraphs})
        conclusion = clean_text(content.get("conclusion"))
        if not introduction or not sections or not conclusion:
            return None
        return {"introduction": introduction, "sections": sections, "conclusion": conclusion}
    return None


def sanitize_blocks(raw_blocks):
    """Valide et normalise les blocs du modèle. Ne fabrique jamais de contenu."""
    clean_blocks = []
    if isinstance(raw_blocks, list):
        for item in raw_blocks[:MAX_BLOCKS]:
            if not isinstance(item, dict):
                continue
            block_type = item.get("type")
            normalized = normalize_block_content(block_type, item.get("content"))
            if normalized is None:
                continue
            title = item.get("title")
            clean_blocks.append(
                {
                    "type": block_type,
                    "title": title.strip()[:200] if isinstance(title, str) else "",
                    "content": normalized,
                }
            )
    return clean_blocks


def rescue_procedure(fallback_steps):
    """Présentation de secours honnête : numérote les étapes du modèle, sans rien inventer."""
    if not isinstance(fallback_steps, list):
        return None
    steps = [s.strip() for s in fallback_steps if isinstance(s, str) and s.strip()]
    if not steps:
        return None
    return {
        "type": "procedure",
        "title": "",
        "content": {"steps": [{"title": s, "detail": ""} for s in steps[:MAX_PLAN_STEPS]]},
    }


def diagnose_blocks(raw_blocks):
    """Résumé structurel (sans contenu) pour diagnostiquer les blocs rejetés."""
    if not isinstance(raw_blocks, list):
        return {"reçus": 0, "forme": type(raw_blocks).__name__}
    types = [item.get("type") if isinstance(item, dict) else type(item).__name__ for item in raw_blocks]
    return {"reçus": len(raw_blocks), "types": types}


def finalize_plan(plan: PlanResponse) -> PlanResponse:
    """Normalise les champs texte en conservant les blocs validés."""
    clean_title = plan.title.strip()
    clean_steps = [step.strip() for step in plan.steps if step.strip()]
    clean_questions = [question.strip() for question in plan.questions if question.strip()]
    if not clean_title or not clean_steps or len(clean_steps) > MAX_PLAN_STEPS or len(clean_questions) > MAX_PLAN_QUESTIONS:
        raise ValueError("plan invalide après normalisation")
    return PlanResponse(title=clean_title, steps=clean_steps, questions=clean_questions, blocks=plan.blocks)


def describe_wav(data):
    """Valide un WAV en mémoire. Retourne (canaux, octets/échantillon, fréquence, secondes)."""
    if not isinstance(data, bytes) or not data:
        raise ValueError("audio vide")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.getnframes()
    except (wave.Error, EOFError) as error:
        raise ValueError("fichier WAV illisible") from error
    if channels != 1:
        raise ValueError("le WAV doit être mono")
    if width != 2:
        raise ValueError("le WAV doit être en 16 bits")
    if rate not in ASR_SAMPLE_RATES:
        raise ValueError("fréquence acceptée : 16 ou 24 kHz")
    if frames <= 0:
        raise ValueError("audio vide")
    seconds = frames / rate
    if seconds > MAX_AUDIO_SECONDS:
        raise ValueError("audio de plus de 10 minutes")
    return channels, width, rate, seconds


def _image_signature_ok(mime, raw):
    """Vérifie la signature binaire d'une image décodée (bibliothèque standard)."""
    if mime in ("image/jpeg", "image/jpg"):
        return raw[:3] == b"\xff\xd8\xff"
    if mime == "image/png":
        return raw[:8] == b"\x89PNG\r\n\x1a\n"
    if mime == "image/gif":
        return raw[:6] in (b"GIF87a", b"GIF89a")
    if mime == "image/webp":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    if mime == "image/x-icon":
        return raw[:4] == b"\x00\x00\x01\x00"
    return False


def decode_chat_image(image):
    """Valide et décode l'image d'un message. Retourne (mime, octets)."""
    mime = image.mime.strip().lower()
    if mime not in CHAT_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=422,
            detail="Accepted image formats: JPEG, PNG, GIF, WebP, or icon.",
        )
    try:
        raw = base64.b64decode("".join(image.data.split()), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status_code=422,
            detail="Invalid image: incorrect Base64 content.",
        )
    if not raw:
        raise HTTPException(status_code=422, detail="Invalid image: empty content.")
    if len(raw) > MAX_CHAT_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Image too large: 5 MB maximum.",
        )
    if not _image_signature_ok(mime, raw):
        raise HTTPException(
            status_code=422,
            detail="Invalid image: content does not match the declared format.",
        )
    return mime, raw


def _jpeg_dimensions(raw):
    """Lit (largeur, hauteur) d'un JPEG via ses marqueurs SOF (bibliothèque standard)."""
    pos = 2
    end = len(raw)
    while pos + 4 <= end:
        if raw[pos] != 0xFF:
            pos += 1
            continue
        marker = raw[pos + 1]
        if marker == 0x00 or marker == 0xFF:
            pos += 1
            continue
        if marker == 0xD8 or 0xD0 <= marker <= 0xD9:
            pos += 2
            continue
        if marker == 0xDA:
            return None
        size = int.from_bytes(raw[pos + 2:pos + 4], "big")
        if size < 2 or pos + 2 + size > end:
            return None
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if size < 7:
                return None
            height = int.from_bytes(raw[pos + 5:pos + 7], "big")
            width = int.from_bytes(raw[pos + 7:pos + 9], "big")
            if width > 0 and height > 0:
                return width, height
            return None
        pos += 2 + size
    return None


def _webp_dimensions(raw):
    """Lit (largeur, hauteur) d'un WebP (VP8, VP8L ou VP8X)."""
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
        return None
    if len(raw) < 20:
        return None
    chunk = raw[12:16]
    if chunk == b"VP8X":
        if len(raw) < 30:
            return None
        width = int.from_bytes(raw[24:27], "little") + 1
        height = int.from_bytes(raw[27:30], "little") + 1
    elif chunk == b"VP8 ":
        if len(raw) < 30 or raw[23:26] != b"\x9d\x01\x2a":
            return None
        width = int.from_bytes(raw[26:28], "little") & 0x3FFF
        height = int.from_bytes(raw[28:30], "little") & 0x3FFF
    elif chunk == b"VP8L":
        if len(raw) < 25 or raw[20] != 0x2F:
            return None
        bits = int.from_bytes(raw[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
    else:
        return None
    if width > 0 and height > 0:
        return width, height
    return None


def _image_dimensions(mime, raw):
    """Lit les dimensions natives d'une image décodée. Lève 422 si illisibles."""
    dims = None
    try:
        if mime == "image/png":
            if len(raw) >= 24 and raw[12:16] == b"IHDR":
                width = int.from_bytes(raw[16:20], "big")
                height = int.from_bytes(raw[20:24], "big")
                if width > 0 and height > 0:
                    dims = (width, height)
        elif mime == "image/gif":
            if len(raw) >= 10:
                width = int.from_bytes(raw[6:8], "little")
                height = int.from_bytes(raw[8:10], "little")
                if width > 0 and height > 0:
                    dims = (width, height)
        elif mime in ("image/jpeg", "image/jpg"):
            dims = _jpeg_dimensions(raw)
        elif mime == "image/webp":
            dims = _webp_dimensions(raw)
        elif mime == "image/x-icon":
            if len(raw) >= 8:
                dims = (raw[6] or 256, raw[7] or 256)
    except (IndexError, ValueError):
        dims = None
    if dims is None:
        raise HTTPException(status_code=422, detail="Invalid image: unreadable dimensions.")
    return dims


def _finite_box(values):
    """Vérifie un quadruplet (gauche, haut, droite, bas) fini et non vide."""
    if len(values) != 4:
        return False
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        return False
    return values[2] > values[0] and values[3] > values[1]


def _parse_segmentation_result(result):
    """Convertit le résultat SAM en (matches, truncated). Lève 502 si inexploitable."""
    if getattr(getattr(result, "outcome", None), "status", None) != "completed":
        raise HTTPException(status_code=502, detail="Segmentation interrupted: try again.")
    for diagnostic in getattr(result, "diagnostics", []) or []:
        if getattr(diagnostic, "severity", None) == "error":
            raise HTTPException(
                status_code=502, detail="Segmentation unusable: try again."
            )
    boxes = {}
    masks = {}
    order = []
    for record in getattr(result, "records", []) or []:
        kind = getattr(record, "kind", None)
        object_id = getattr(record, "object_id", None)
        if not isinstance(object_id, str) or not object_id:
            continue
        if kind == "box":
            coords = (
                getattr(record, "left", None),
                getattr(record, "top", None),
                getattr(record, "right", None),
                getattr(record, "bottom", None),
            )
            if not _finite_box(coords):
                continue
            if object_id not in boxes:
                boxes[object_id] = record
                if object_id not in masks:
                    order.append(object_id)
        elif kind == "mask":
            if object_id not in masks:
                masks[object_id] = record
                if object_id not in boxes:
                    order.append(object_id)
    matches = []
    truncated = False
    total_svg = 0
    for object_id in order:
        box_record = boxes.get(object_id)
        if box_record is None:
            continue
        if len(matches) >= MAX_SEGMENT_MATCHES:
            truncated = True
            break
        mask_payload = None
        mask_record = masks.get(object_id)
        if mask_record is not None:
            raster = getattr(mask_record, "mask", None)
            bounds = getattr(mask_record, "bounds", None)
            raster_ok = (
                raster is not None
                and isinstance(getattr(raster, "width", None), int)
                and isinstance(getattr(raster, "height", None), int)
                and raster.width > 0
                and raster.height > 0
            )
            bounds_ok = bounds is not None and _finite_box(
                (
                    getattr(bounds, "left", None),
                    getattr(bounds, "top", None),
                    getattr(bounds, "right", None),
                    getattr(bounds, "bottom", None),
                )
            )
            if not (raster_ok and bounds_ok):
                mask_payload = None
            else:
                try:
                    svg = decode_mask_to_svg_path(raster)
                except (InvalidSegmentationMaskError, ValueError, RuntimeError):
                    svg = ""
                if svg and isinstance(svg, str):
                    if (
                        len(svg) <= MAX_SEGMENT_SVG_CHARS
                        and total_svg + len(svg) <= MAX_SEGMENT_TOTAL_SVG_CHARS
                    ):
                        total_svg += len(svg)
                        mask_payload = SegmentMask(
                            width=raster.width,
                            height=raster.height,
                            bounds=SegmentBox(
                                left=bounds.left,
                                top=bounds.top,
                                right=bounds.right,
                                bottom=bounds.bottom,
                            ),
                            svg=svg,
                        )
                    else:
                        truncated = True
        confidence = getattr(box_record, "confidence", None)
        if not (
            isinstance(confidence, (int, float))
            and math.isfinite(confidence)
            and 0 <= confidence <= 1
        ):
            confidence = None
        matches.append(
            SegmentMatch(
                object_id=object_id,
                box=SegmentBox(
                    left=box_record.left,
                    top=box_record.top,
                    right=box_record.right,
                    bottom=box_record.bottom,
                ),
                confidence=confidence,
                mask=mask_payload,
            )
        )
    return matches, truncated


def build_chat_input(turns, data_url=None):
    """Construit le paramètre input : chaîne texte, ou messages multimodaux si image."""
    if data_url is None:
        return format_chat_history(turns)
    items = []
    for role, text in turns[:-1]:
        block = "output_text" if role == "assistant" else "input_text"
        items.append(
            {"type": "message", "role": role, "content": [{"type": block, "text": text}]}
        )
    last_role, last_text = turns[-1]
    last_block = "output_text" if last_role == "assistant" else "input_text"
    items.append(
        {
            "type": "message",
            "role": last_role,
            "content": [
                {"type": last_block, "text": last_text},
                {"type": "input_image", "image_url": data_url},
            ],
        }
    )
    return items


def format_chat_history(turns):
    """Rend l'historique [(role, texte)] en transcript lisible pour le modèle."""
    lines = []
    for role, text in turns:
        speaker = "Utilisateur" if role == "user" else "Assistant"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


@app.get("/")
def read_root():
    """Route d'accueil : confirme que le backend tourne."""
    return {
        "project": "ReFlow",
        "message": "Welcome to ReFlow!",
        "status": "running",
        "version": app.version,
    }


@app.get("/health")
def health_check():
    """Route de santé : utile pour vérifier rapidement que l'API répond."""
    return {"status": "ok"}


@app.get("/ai-test")
def ai_test():
    """Premier appel réel à Muse Spark. Aucun outil, aucune action système."""
    client = get_client()

    try:
        response = client.responses.create(
            model=MODEL_NAME,
            instructions=(
                "You are the ReFlow assistant, an AI agent that turns messy requests "
                "into action plans validated by the user. "
                "Always reply in English."
            ),
            input="Present ReFlow in one sentence.",
        )
    except AuthenticationError as error:
        raise api_failure(
            401, "Authentication refused: check that your key in .env is correct.", error
        )
    except PermissionDeniedError as error:
        raise api_failure(
            403, "Access denied: your key is not allowed to use this model or API.", error
        )
    except NotFoundError as error:
        raise api_failure(
            404, f"Model or route not found: check the model name ({MODEL_NAME}).", error
        )
    except RateLimitError as error:
        raise api_failure(
            429, "Request quota or rate limit reached: wait a moment or check your credits.", error
        )
    except APITimeoutError as error:
        raise api_failure(504, "The Meta API did not respond in time. Try again.", error)
    except APIConnectionError as error:
        raise api_failure(
            503, "Cannot connect to the Meta API: check your internet connection.", error
        )
    except APIStatusError as error:
        raise api_failure(502, "The Meta API returned an error. Check the server logs.", error)

    raw_output = getattr(response, "output_text", "")
    text = raw_output.strip() if isinstance(raw_output, str) else ""
    if not text:
        raise HTTPException(status_code=502, detail="The model returned an empty reply.")

    return {"model": MODEL_NAME, "response": text}


@app.post("/plan", response_model=PlanResponse)
def create_plan(body: PlanRequest):
    """Transforme une demande libre en plan d'action. Proposition seule : aucune action externe."""
    demande = body.request.strip()
    if not demande:
        raise HTTPException(status_code=422, detail="The request must not be empty.")

    client = get_client()

    base_instructions = (
                "You are the ReFlow assistant, an AI agent that turns messy requests "
                "into action plans the user will validate before anything runs. "
                "Always reply in English, even if the request is written in French or another "
                "language: understand the request but answer in English. "
                "ReFlow only suggests a plan: never claim to have executed an action, "
                "sent an email, booked anything or modified a file. "
                "Never invent dates, deadlines, prices, times or proper nouns: only use "
                "information from the request, or relative labels (Day 1, Step 1). "
                "Reply with a strict JSON object, no surrounding text or code block. "
                "The object always contains title (short title), steps (1 to 20 steps: each starts "
                "with an action verb, is concrete and verifiable, logical order) and questions "
                "(0 to 3 questions, only if their answers would change the plan). "
                "Choose the format from the request: comparison to table and decision ; "
                "project organization to tree ; explanation or procedure to procedure ; "
                "schedule to schedule ; decision analysis to decision ; summary to summary ; "
                "explicit management of tasks to do to checklist ; "
                "full-text writing to article. "
                "The checklist is reserved for that last case. "
                "The blocks field is mandatory: always return 1 to 3 valid blocks. "
                "A reply without blocks is invalid. "
                "For a comparison, the table is mandatory with one row per option and the real criteria. "
                "The steps summarize the actionable conclusions, never phrases like \"examine the table\". "
                "For a writing request, produce the full text directly in an article block: "
                "complete paragraphs, never a plan or steps ; steps then list the real structure "
                "of the produced text (part headings), without inventing actions ; no questions if the request suffices. "
                "Each block has a type, a title and content. Possible types: "
                "checklist with {items: [text]} ; table with {headers: [text], rows: [[text]]} ; "
                "tree with {nodes: [{label, children: [...]}]} ; timeline with {events: [{label, detail}]} ; "
                "decision with {options: [{name, pros: [text], cons: [text]}], recommendation} ; "
                "procedure with {steps: [{title, detail}]} ; schedule with {days: [{label, tasks: [text]}]} ; "
                "summary with {points: [text], conclusion} ; "
                "article with {introduction, sections: [{heading, paragraphs: [text]}], conclusion}. "
                "Example: {\"title\": \"Choose a frontend framework\", "
                "\"steps\": [\"List the selection criteria\", \"Compare the options\", "
                "\"Test the favorite on a prototype\"], \"questions\": [], \"blocks\": "
                "[{\"type\": \"decision\", \"title\": \"Comparison\", \"content\": {\"options\": "
                "[{\"name\": \"React\", \"pros\": [\"Vast ecosystem\"], \"cons\": [\"Learning "
                "curve\"]}], \"recommendation\": \"Choose according to your criteria\"}}]}. "
                "Match the number and type of blocks to the request ; add no other field."
            )
    repair_instructions = base_instructions + (
        " IMPORTANT CORRECTION: your previous reply was invalid because it "
        "contained no presentation blocks. Start over and always include "
        "1 to 3 valid blocks with their real, complete content: for example a "
        "filled table for a comparison. Do not describe the format: produce it."
    )
    try:
        response = client.responses.create(
            model=MODEL_NAME,
            instructions=base_instructions,
            input=demande,
        )
    except AuthenticationError as error:
        raise api_failure(
            401, "Authentication refused: check that your key in .env is correct.", error
        )
    except PermissionDeniedError as error:
        raise api_failure(
            403, "Access denied: your key is not allowed to use this model or API.", error
        )
    except NotFoundError as error:
        raise api_failure(
            404, f"Model or route not found: check the model name ({MODEL_NAME}).", error
        )
    except RateLimitError as error:
        raise api_failure(
            429, "Request quota or rate limit reached: wait a moment or check your credits.", error
        )
    except APITimeoutError as error:
        raise api_failure(504, "The Meta API did not respond in time. Try again.", error)
    except APIConnectionError as error:
        raise api_failure(
            503, "Cannot connect to the Meta API: check your internet connection.", error
        )
    except APIStatusError as error:
        raise api_failure(502, "The Meta API returned an error. Check the server logs.", error)

    raw_output = getattr(response, "output_text", "")
    text = raw_output.strip() if isinstance(raw_output, str) else ""
    if not text:
        raise HTTPException(status_code=502, detail="The model returned an empty reply.")

    cleaned = text
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError("la réponse JSON n'est pas un objet")
        if data.get("questions") is None:
            data["questions"] = []
        # Les blocs sont normalisés par type ; à défaut, un nouvel essai est
        # demandé au modèle, puis une présentation de secours honnête.
        raw_blocks_seen = data.get("blocks")
        data["blocks"] = sanitize_blocks(raw_blocks_seen)
        plan = PlanResponse(**data)
        if not plan.blocks:
            logger.info(
                "Blocs absents : clés=%s, diagnostic=%s — nouvel essai demandé",
                sorted(data.keys()),
                diagnose_blocks(raw_blocks_seen),
            )
            try:
                response = client.responses.create(
                    model=MODEL_NAME,
                    instructions=repair_instructions,
                    input=demande,
                )
            except AuthenticationError as error:
                raise api_failure(
                    401, "Authentication refused: check that your key in .env is correct.", error
                )
            except PermissionDeniedError as error:
                raise api_failure(
                    403, "Access denied: your key is not allowed to use this model or API.", error
                )
            except NotFoundError as error:
                raise api_failure(
                    404, f"Model or route not found: check the model name ({MODEL_NAME}).", error
                )
            except RateLimitError as error:
                raise api_failure(
                    429, "Request quota or rate limit reached: wait a moment or check your credits.", error
                )
            except APITimeoutError as error:
                raise api_failure(504, "The Meta API did not respond in time. Try again.", error)
            except APIConnectionError as error:
                raise api_failure(
                    503, "Cannot connect to the Meta API: check your internet connection.", error
                )
            except APIStatusError as error:
                raise api_failure(502, "The Meta API returned an error. Check the server logs.", error)

            raw_output = getattr(response, "output_text", "")
            text = raw_output.strip() if isinstance(raw_output, str) else ""
            if not text:
                raise HTTPException(status_code=502, detail="The model returned an empty reply.")

            cleaned = text
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()[1:]
                if lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()

            data = json.loads(cleaned)
            if not isinstance(data, dict):
                raise ValueError("la réponse JSON n'est pas un objet")
            if data.get("questions") is None:
                data["questions"] = []
            data["blocks"] = sanitize_blocks(data.get("blocks"))
            plan = PlanResponse(**data)
            if not plan.blocks:
                rescued = rescue_procedure(data.get("steps", []))
                if rescued is None:
                    raise HTTPException(
                        status_code=502, detail="The model returned an invalid plan. Try again."
                    )
                data["blocks"] = [rescued]
                plan = PlanResponse(**data)
    except (ValueError, ValidationError) as error:
        raise api_failure(502, "The model returned an invalid plan. Try again.", error)

    try:
        final = finalize_plan(plan)
    except ValueError:
        logger.error("Plan invalide du modèle (longueur=%s)", len(text))
        raise HTTPException(status_code=502, detail="The model returned an invalid plan. Try again.")

    logger.info("Plan final : %d étapes, %d blocs retenus", len(final.steps), len(final.blocks))
    return final


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(request: Request):
    """Transcrit un WAV brut (audio/wav) via Meta Voice Transcribe. Zéro fichier disque."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ASR_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Accepted format: mono 16-bit PCM WAV at 16 or 24 kHz.",
        )
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            too_big = int(declared) > MAX_AUDIO_BYTES
        except ValueError:
            too_big = False
        if too_big:
            raise HTTPException(status_code=413, detail="Audio too large: 32 MB maximum.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio too large: 32 MB maximum.")
    data = bytes(body)
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    try:
        _channels, _width, sample_rate, seconds = describe_wav(data)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid WAV: mono 16-bit PCM at 16 or 24 kHz, 10 minutes maximum.",
        )

    client = get_client()
    request_part = json.dumps(
        {"mode": ASR_MODE, "model": ASR_MODEL_NAME, "audioEncoding": ASR_AUDIO_ENCODING}
    )
    try:
        # Appel synchrone isolé : ne bloque pas la boucle d'événements.
        result = await asyncio.to_thread(
            client.post,
            META_ASR_URL,
            cast_to=dict,
            files=[
                ("request", (None, request_part, "application/json")),
                ("audio", ("recording.wav", data, "audio/wav")),
            ],
            options={
                "headers": {"Content-Type": "multipart/form-data"},
                "timeout": ASR_TIMEOUT,
            },
        )
    except AuthenticationError as error:
        raise api_failure(
            401, "Authentication refused: check that your key in .env is correct.", error
        )
    except PermissionDeniedError as error:
        raise api_failure(
            403, "Access denied: your key is not allowed to use this model or API.", error
        )
    except NotFoundError as error:
        raise api_failure(
            404, f"Voice route not found: check the Meta URL ({META_ASR_URL}).", error
        )
    except RateLimitError as error:
        raise api_failure(
            429, "Request quota or rate limit reached: wait a moment or check your credits.", error
        )
    except APITimeoutError as error:
        raise api_failure(504, "Transcription timed out. Try again.", error)
    except APIConnectionError as error:
        raise api_failure(
            503, "Cannot connect to the Meta API: check your internet connection.", error
        )
    except APIStatusError as error:
        raise api_failure(502, "Transcription failed. Try again.", error)

    transcript = result.get("transcript", "") if isinstance(result, dict) else ""
    transcript = transcript.strip() if isinstance(transcript, str) else ""
    if not transcript:
        raise HTTPException(status_code=502, detail="The model returned an empty transcript.")

    logger.info(
        "Transcription : %d caractères (audio %ds à %d Hz)",
        len(transcript),
        int(seconds),
        sample_rate,
    )
    return TranscribeResponse(transcript=transcript)


@app.post("/chat", response_model=ChatResponse)
def create_chat(body: ChatRequest):
    """Répond naturellement en tenant compte des derniers échanges, avec image éventuelle."""
    recent = body.messages[-MAX_CHAT_HISTORY:]
    for message in recent:
        if message.role == "assistant" and message.image is not None:
            raise HTTPException(
                status_code=422,
                detail="Assistant messages cannot contain an image.",
            )
    # Seule l'image du message courant est transmise ; les anciennes images
    # de l'historique ne sont jamais renvoyées au modèle.
    data_url = None
    current = recent[-1]
    if current.image is not None and current.role == "user":
        mime, raw = decode_chat_image(current.image)
        data_url = "data:{0};base64,{1}".format(mime, base64.b64encode(raw).decode("ascii"))
    turns = [(message.role, message.text.strip()) for message in recent]
    turns = [(role, text) for role, text in turns if text]
    if data_url is not None and not current.text.strip():
        # Message courant = image seule : tour porteur pour l'image.
        turns.append(("user", CHAT_IMAGE_ONLY_TEXT))
    if not turns:
        raise HTTPException(status_code=422, detail="No usable messages.")
    chat_input = build_chat_input(turns, data_url)

    client = get_client()

    chat_instructions = (
        "You are ReFlow, a warm conversational assistant. "
        "Always reply in English, even if the user writes in French or another "
        "language: understand the request but answer in English. If the user "
        "explicitly asks for another language, honor that request. "
        "The \"Utilisateur\" and \"Assistant\" labels structure the history: "
        "ignore their language. Reply naturally, concisely and "
        "conversationally, taking previous exchanges into account. Ask "
        "clarifying questions when they would help. "
        "Never produce a JSON plan or a list of actionable steps unless "
        "the user explicitly asks: this is a discussion, not plan "
        "generation."
    )
    try:
        response = client.responses.create(
            model=MODEL_NAME,
            instructions=(
                chat_instructions + " " + CHAT_IMAGE_INSTRUCTIONS
                if data_url is not None
                else chat_instructions
            ),
            input=chat_input,
        )
    except AuthenticationError as error:
        raise api_failure(
            401, "Authentication refused: check that your key in .env is correct.", error
        )
    except PermissionDeniedError as error:
        raise api_failure(
            403, "Access denied: your key is not allowed to use this model or API.", error
        )
    except NotFoundError as error:
        raise api_failure(
            404, f"Model or route not found: check the model name ({MODEL_NAME}).", error
        )
    except RateLimitError as error:
        raise api_failure(
            429, "Request quota or rate limit reached: wait a moment or check your credits.", error
        )
    except APITimeoutError as error:
        raise api_failure(504, "The Meta API did not respond in time. Try again.", error)
    except APIConnectionError as error:
        raise api_failure(
            503, "Cannot connect to the Meta API: check your internet connection.", error
        )
    except APIStatusError as error:
        raise api_failure(502, "The Meta API returned an error. Check the server logs.", error)

    raw_output = getattr(response, "output_text", "")
    text = raw_output.strip() if isinstance(raw_output, str) else ""
    if not text:
        raise HTTPException(status_code=502, detail="The model returned an empty reply.")

    logger.info(
        "Chat : %d échanges%s, réponse %d caractères",
        len(turns),
        " + image" if data_url is not None else "",
        len(text),
    )
    return ChatResponse(reply=text)


def _parse_generated_image(response):
    """Extrait (mime, octets) d'une réponse images (génération ou édition)."""
    entries = getattr(response, "data", None)
    entry = entries[0] if isinstance(entries, list) and entries else None
    if isinstance(entry, dict):
        b64 = entry.get("b64_json")
    else:
        b64 = getattr(entry, "b64_json", None)
    if not b64 or not isinstance(b64, str):
        raise HTTPException(status_code=502, detail="The model did not return any image.")
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status_code=502, detail="The image returned by the model is unusable."
        )
    if not raw or len(raw) > MAX_GENERATED_IMAGE_BYTES:
        raise HTTPException(
            status_code=502, detail="The image returned by the model is unusable."
        )
    mime = _detect_generated_mime(raw)
    if mime is None:
        raise HTTPException(
            status_code=502, detail="The image returned by the model is unusable."
        )
    return mime, raw


def _detect_generated_mime(raw):
    """Déduit le MIME d'une image générée depuis sa signature binaire."""
    for mime in ("image/png", "image/jpeg", "image/webp"):
        if _image_signature_ok(mime, raw):
            return mime
    return None


@app.post("/image", response_model=ImageResponse)
def create_image(body: ImageRequest):
    """Génère une image avec Muse Image à partir d'une description."""
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="Describe the image to generate.")
    if body.size is not None and body.size not in IMAGE_ALLOWED_SIZES:
        raise HTTPException(
            status_code=422,
            detail="Accepted sizes: auto, 1792x1024, 1024x1536, or 1536x1024.",
        )
    client = get_client()
    call_kwargs = {
        "model": IMAGE_MODEL_NAME,
        "prompt": prompt,
        "n": 1,
        "response_format": "b64_json",
    }
    if body.size is not None:
        call_kwargs["size"] = body.size
    try:
        response = client.images.generate(**call_kwargs)
    except AuthenticationError as error:
        raise api_failure(
            401, "Authentication refused: check that your key in .env is correct.", error
        )
    except PermissionDeniedError as error:
        raise api_failure(
            403, "Access denied: your key is not allowed to use this model or API.", error
        )
    except NotFoundError as error:
        raise api_failure(
            404, f"Model or route not found: check the model name ({IMAGE_MODEL_NAME}).", error
        )
    except RateLimitError as error:
        raise api_failure(
            429, "Request quota or rate limit reached: wait a moment or check your credits.", error
        )
    except APITimeoutError as error:
        raise api_failure(504, "The Meta API did not respond in time. Try again.", error)
    except APIConnectionError as error:
        raise api_failure(
            503, "Cannot connect to the Meta API: check your internet connection.", error
        )
    except APIStatusError as error:
        if getattr(error, "code", None) == "content_policy_violation":
            raise HTTPException(
                status_code=422,
                detail="Content refused by moderation: rephrase your request.",
            )
        raise api_failure(502, "The Meta API returned an error. Check the server logs.", error)

    mime, raw = _parse_generated_image(response)
    logger.info("Image : prompt %d caractères, %d octets générés", len(prompt), len(raw))
    media_id = save_generated_image(raw, mime)
    return ImageResponse(mime=mime, data=base64.b64encode(raw).decode("ascii"), media_id=media_id)


@app.post("/image/edit", response_model=ImageResponse)
def edit_image(body: ImageEditRequest):
    """Modifie une image existante avec Muse Image selon une instruction."""
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=422, detail="Describe the edit to make.")
    mime, raw = decode_chat_image(body.image)
    filename = "image.{0}".format(EDIT_FILENAME_EXTENSIONS.get(mime, "png"))
    client = get_client()
    try:
        response = client.images.edit(
            model=IMAGE_MODEL_NAME,
            prompt=prompt,
            image=(filename, io.BytesIO(raw), mime),
            n=1,
        )
    except AuthenticationError as error:
        raise api_failure(
            401, "Authentication refused: check that your key in .env is correct.", error
        )
    except PermissionDeniedError as error:
        raise api_failure(
            403, "Access denied: your key is not allowed to use this model or API.", error
        )
    except NotFoundError as error:
        raise api_failure(
            404, f"Model or route not found: check the model name ({IMAGE_MODEL_NAME}).", error
        )
    except RateLimitError as error:
        raise api_failure(
            429, "Request quota or rate limit reached: wait a moment or check your credits.", error
        )
    except APITimeoutError as error:
        raise api_failure(504, "The Meta API did not respond in time. Try again.", error)
    except APIConnectionError as error:
        raise api_failure(
            503, "Cannot connect to the Meta API: check your internet connection.", error
        )
    except APIStatusError as error:
        if getattr(error, "code", None) == "content_policy_violation":
            raise HTTPException(
                status_code=422,
                detail="Content refused by moderation: use another image or rephrase your request.",
            )
        raise api_failure(502, "The Meta API returned an error. Check the server logs.", error)

    out_mime, out_raw = _parse_generated_image(response)
    logger.info(
        "Image éditée : prompt %d caractères, entrée %d octets, sortie %d octets",
        len(prompt),
        len(raw),
        len(out_raw),
    )
    media_id = save_generated_image(out_raw, out_mime)
    return ImageResponse(mime=out_mime, data=base64.b64encode(out_raw).decode("ascii"), media_id=media_id)


@app.get("/media/{media_id}")
def get_media(media_id: str):
    """Serves a persisted ReFlow-generated image (strict id, safe 404)."""
    return media_response(media_id)


@app.post("/documents", response_model=DocumentInfo)
def upload_document(body: DocumentUpload):
    """Imports a PDF/DOCX/TXT document and extracts its text for analysis."""
    try:
        raw = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="The file data is not valid Base64.")
    try:
        return DocumentInfo.model_validate(store_document(body.filename, body.mime, raw))
    except DocumentError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)


@app.get("/documents/{doc_id}", response_model=DocumentInfo)
def get_document(doc_id: str):
    """Returns stored document metadata + preview (for session restore)."""
    try:
        return DocumentInfo.model_validate(document_info(load_document(doc_id)))
    except DocumentError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)


@app.post("/documents/{doc_id}/analyze", response_model=DocumentAnalyzeResponse)
def analyze_document(doc_id: str, body: DocumentAnalyzeRequest):
    """Runs one Document Intelligence operation with Muse Spark."""
    client = get_client()
    try:
        return DocumentAnalyzeResponse.model_validate(
            analyze_stored_document(
                client,
                doc_id,
                body.operation,
                question=body.question,
                style=body.style,
                language=body.language,
            )
        )
    except DocumentError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)


@app.post("/segment", response_model=SegmentResponse)
async def segment_image(body: SegmentRequest):
    """Segmente les occurrences d'un concept dans une image avec SAM 3.1."""
    concept = body.concept.strip()
    if not concept:
        raise HTTPException(
            status_code=422, detail="Describe the object to isolate in a few words."
        )
    mime, raw = decode_chat_image(body.image)
    width, height = _image_dimensions(mime, raw)
    data_url = "data:{0};base64,{1}".format(mime, base64.b64encode(raw).decode("ascii"))
    sam_input = [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": concept},
                {"type": "input_image", "image_url": data_url},
            ],
        }
    ]
    try:
        async with get_async_client() as client:
            stream = await client.responses.create(
                model=SAM_MODEL_NAME, stream=True, input=sam_input
            )
            async with stream:
                result = await parse_responses_stream(
                    stream, image_segmentation_format()
                ).final_result()
    except AuthenticationError as error:
        raise api_failure(
            401, "Authentication refused: check that your key in .env is correct.", error
        )
    except PermissionDeniedError as error:
        raise api_failure(
            403, "Access denied: your key is not allowed to use this model or API.", error
        )
    except NotFoundError as error:
        raise api_failure(
            404, f"Model or route not found: check the model name ({SAM_MODEL_NAME}).", error
        )
    except RateLimitError as error:
        raise api_failure(
            429, "Request quota or rate limit reached: wait a moment or check your credits.", error
        )
    except APITimeoutError as error:
        raise api_failure(504, "The Meta API did not respond in time. Try again.", error)
    except APIConnectionError as error:
        raise api_failure(
            503, "Cannot connect to the Meta API: check your internet connection.", error
        )
    except APIStatusError as error:
        raise api_failure(502, "The Meta API returned an error. Check the server logs.", error)
    except ResponsesStreamError as error:
        logger.error("Segmentation : flux SAM inexploitable (%s)", type(error).__name__)
        raise HTTPException(
            status_code=502, detail="Segmentation unusable: try again."
        )

    matches, truncated = _parse_segmentation_result(result)
    logger.info(
        "Segmentation : concept %d caractères, %d objets (tronqué=%s)",
        len(concept),
        len(matches),
        truncated,
    )
    return SegmentResponse(
        image=SegmentImageSize(width=width, height=height),
        matches=matches,
        truncated=truncated,
    )


def _task_graph_has_cycle(task_ids, edges):
    """Détecte un cycle dans les dépendances (Kahn). edges : id -> [ids préalables]."""
    incoming = {task_id: 0 for task_id in task_ids}
    outgoing = {task_id: [] for task_id in task_ids}
    for target, deps in edges.items():
        for dep in deps:
            outgoing[dep].append(target)
            incoming[target] += 1
    queue = [task_id for task_id in task_ids if incoming[task_id] == 0]
    seen = 0
    while queue:
        node = queue.pop()
        seen += 1
        for nxt in outgoing[node]:
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                queue.append(nxt)
    return seen != len(task_ids)


def _build_agent_plan(data, max_tasks):
    """Valide un dict de plan agentique et calcule les statuts. Lève ValueError/ValidationError."""
    if not isinstance(data, dict):
        raise ValueError("la réponse JSON n'est pas un objet")
    goal = data.get("goal", "")
    goal = goal.strip() if isinstance(goal, str) else ""
    if not goal:
        raise ValueError("objectif manquant")
    summary = data.get("summary", "")
    summary = summary.strip() if isinstance(summary, str) else ""
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("tâches manquantes")
    if len(raw_tasks) > max_tasks:
        raise ValueError(f"le plan contient {len(raw_tasks)} tâches (maximum {max_tasks})")
    tasks = []
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            raise ValueError("tâche invalide")
        tasks.append(PlanTask(**raw))
    task_ids = [task.id.strip() for task in tasks]
    if any(not task_id for task_id in task_ids):
        raise ValueError("identifiant de tâche vide")
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("identifiants de tâche dupliqués")
    raw_missing = data.get("missing_information", [])
    if raw_missing is None:
        raw_missing = []
    if not isinstance(raw_missing, list):
        raise ValueError("informations manquantes invalides")
    missing = []
    for raw in raw_missing:
        if not isinstance(raw, dict):
            raise ValueError("information manquante invalide")
        missing.append(MissingInfo(**raw))
    missing_ids = [info.id.strip() for info in missing]
    if any(not info_id for info_id in missing_ids):
        raise ValueError("identifiant d'information manquante vide")
    if len(set(missing_ids)) != len(missing_ids):
        raise ValueError("identifiants d'information manquante dupliqués")
    id_set = set(task_ids)
    missing_set = set(missing_ids)
    edges = {}
    normalized_tasks = []
    for task, task_id in zip(tasks, task_ids):
        title = task.title.strip()
        if not title:
            raise ValueError(f"tâche {task_id} sans titre")
        criteria = [
            criterion.strip()
            for criterion in task.success_criteria
            if isinstance(criterion, str) and criterion.strip()
        ]
        if not criteria:
            raise ValueError(f"tâche {task_id} sans critère de réussite")
        deps = [dep.strip() for dep in task.depends_on if isinstance(dep, str)]
        deps = [dep for dep in deps if dep]
        if task_id in deps:
            raise ValueError(f"tâche {task_id} dépend d'elle-même")
        unknown = [dep for dep in deps if dep not in id_set]
        if unknown:
            raise ValueError(f"tâche {task_id} dépend de tâche inconnue : {unknown[0]}")
        refs = [ref.strip() for ref in task.needs_info if isinstance(ref, str)]
        refs = [ref for ref in refs if ref]
        unknown_refs = [ref for ref in refs if ref not in missing_set]
        if unknown_refs:
            raise ValueError(
                f"tâche {task_id} référence une information inconnue : {unknown_refs[0]}"
            )
        edges[task_id] = deps
        normalized_tasks.append((task, task_id, title, criteria, deps))
    if _task_graph_has_cycle(task_ids, edges):
        raise ValueError("dépendances cycliques entre tâches")
    normalized_missing = []
    for info, info_id in zip(missing, missing_ids):
        field = info.field.strip()
        question = info.question.strip()
        if not field or not question:
            raise ValueError(f"information {info_id} incomplète")
        blocked = [bid.strip() for bid in info.blocks_task_ids if isinstance(bid, str)]
        blocked = [bid for bid in blocked if bid]
        unknown_blocked = [bid for bid in blocked if bid not in id_set]
        if unknown_blocked:
            raise ValueError(
                f"information {info_id} bloque une tâche inconnue : {unknown_blocked[0]}"
            )
        normalized_missing.append((info_id, field, question, blocked))
    blocked_ids = set()
    for _, _, _, blocked in normalized_missing:
        blocked_ids.update(blocked)
    final_tasks = []
    for task, task_id, title, criteria, deps in normalized_tasks:
        auto_refs = sorted(
            info_id
            for info_id, _, _, blocked in normalized_missing
            if task_id in blocked
        )
        if task_id in blocked_ids:
            status = TaskStatus.BLOCKED
        elif not deps:
            status = TaskStatus.READY
        else:
            status = TaskStatus.PENDING
        detail = task.detail.strip() if isinstance(task.detail, str) else ""
        final_tasks.append(
            PlanTask(
                id=task_id,
                title=title,
                detail=detail,
                depends_on=deps,
                success_criteria=criteria,
                needs_info=auto_refs,
                status=status,
            )
        )
    final_missing = [
        MissingInfo(id=info_id, field=field, question=question, blocks_task_ids=blocked)
        for info_id, field, question, blocked in normalized_missing
    ]
    raw_questions = data.get("questions", [])
    if raw_questions is None:
        raw_questions = []
    if not isinstance(raw_questions, list):
        raise ValueError("questions invalides")
    questions = [q.strip() for q in raw_questions if isinstance(q, str) and q.strip()]
    if len(questions) > MAX_AGENT_QUESTIONS:
        raise ValueError("trop de questions de clarification")
    return AgentPlanResponse(
        goal=goal,
        summary=summary,
        tasks=final_tasks,
        missing_information=final_missing,
        questions=questions,
        status=PlanStatus.NEEDS_INFO if final_missing else PlanStatus.READY,
        blocks=sanitize_blocks(data.get("blocks")),
    )


def _request_model_text(client, instructions, demande):
    """Appelle Muse Spark et retourne le texte de sortie. Lève HTTPException contrôlée."""
    try:
        response = client.responses.create(
            model=MODEL_NAME,
            instructions=instructions,
            input=demande,
        )
    except AuthenticationError as error:
        raise api_failure(
            401, "Authentication refused: check that your key in .env is correct.", error
        )
    except PermissionDeniedError as error:
        raise api_failure(
            403, "Access denied: your key is not allowed to use this model or API.", error
        )
    except NotFoundError as error:
        raise api_failure(
            404, f"Model or route not found: check the model name ({MODEL_NAME}).", error
        )
    except RateLimitError as error:
        raise api_failure(
            429, "Request quota or rate limit reached: wait a moment or check your credits.", error
        )
    except APITimeoutError as error:
        raise api_failure(504, "The Meta API did not respond in time. Try again.", error)
    except APIConnectionError as error:
        raise api_failure(
            503, "Cannot connect to the Meta API: check your internet connection.", error
        )
    except APIStatusError as error:
        raise api_failure(502, "The Meta API returned an error. Check the server logs.", error)

    raw_output = getattr(response, "output_text", "")
    text = raw_output.strip() if isinstance(raw_output, str) else ""
    if not text:
        raise HTTPException(status_code=502, detail="The model returned an empty reply.")
    return text


@app.get("/actions")
def list_actions():
    """Lists the execution action types supported in Phase 4 v1."""
    return {"actions": action_specs()}


@app.post("/execute", response_model=ExecuteResponse)
def execute_task(body: ExecuteRequest):
    """Executes one approved task action (Phase 4, single-flight)."""
    try:
        return run_execution(body)
    except ExecutionError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail)


def _parse_model_json(text):
    """Extrait l'objet JSON d'une sortie modèle (tolère un bloc de code). Lève ValueError."""
    cleaned = text
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("la réponse JSON n'est pas un objet")
    return data


@app.post("/plan/structured", response_model=AgentPlanResponse)
def create_structured_plan(body: AgentPlanRequest):
    """Transforme une demande libre en plan agentique structuré (aucune exécution)."""
    demande = body.request.strip()
    if not demande:
        raise HTTPException(status_code=422, detail="The request must not be empty.")
    max_tasks = body.max_tasks
    client = get_client()

    base_instructions = (
        "You are the ReFlow planning engine, an AI agent that turns "
        "messy requests into action plans the user will validate "
        "before anything runs. "
        "Always reply in English, even if the request is written in French or another "
        "language: understand the request but answer in English. "
        "Analyze the request: restate its goal in one sentence (goal) and "
        "summarize the plan in two sentences at most (summary). "
        "Break the request into distinct tasks, at most " + str(max_tasks) + ": "
        "each has a unique id (t1, t2, ...), a title starting with "
        "an action verb, an optional detail, depends_on (ids of "
        "prerequisite tasks, [] if none), success_criteria (1 to 8 concrete "
        "and verifiable conditions). "
        "needs_info is computed: leave it empty. "
        "List the missing information blocking the plan "
        "(missing_information: unique id m1, ..., short field name, question, "
        "blocks_task_ids) ; empty list if the request suffices. "
        "questions: 0 to 3 simple clarifying questions, only if "
        "their answers would change the plan. "
        "Return no statuses: they are computed. "
        "ReFlow only suggests a plan: never claim to have executed "
        "an action, sent an email, booked anything or modified a file. "
        "Never invent dates, deadlines, prices, times or proper nouns: "
        "only use information from the request, or relative labels "
        "(Day 1, Step 1). "
        "Reply with a strict JSON object, no surrounding text or code block, "
        "with exactly the fields goal, summary, tasks, missing_information, "
        "questions and blocks. "
        "blocks is optional (0 to 2 presentation blocks with type, title, "
        "content) ; possible types: checklist {items}, table {headers, rows}, "
        "tree {nodes}, timeline {events}, decision {options, recommendation}, "
        "procedure {steps}, schedule {days}, summary {points, conclusion}, "
        "article {introduction, sections, conclusion}. "
        "Example: {\"goal\": \"Prepare a move\", \"summary\": \"Sort then "
        "book the transport.\", \"tasks\": [{\"id\": \"t1\", \"title\": \"List the "
        "rooms to empty\", \"detail\": \"\", \"depends_on\": [], \"success_criteria\": "
        "[\"Each room has a list\"], \"needs_info\": []}, {\"id\": \"t2\", "
        "\"title\": \"Book a van\", \"detail\": \"\", \"depends_on\": [\"t1\"], "
        "\"success_criteria\": [\"Van booked for a date\"], \"needs_info\": []}], "
        "\"missing_information\": [], \"questions\": [], \"blocks\": []}."
    )
    repair_instructions = base_instructions + (
        " IMPORTANT CORRECTION: your previous reply was invalid (malformed "
        "JSON, inconsistent graph or missing field). Start over: strict JSON "
        "with the required fields, unique ids, dependencies to existing "
        "tasks, no cycles, concrete criteria."
    )
    try:
        text = _request_model_text(client, base_instructions, demande)
        plan = _build_agent_plan(_parse_model_json(text), max_tasks)
    except (ValueError, ValidationError):
        logger.info("Plan structuré invalide — nouvel essai demandé")
        text = _request_model_text(client, repair_instructions, demande)
        try:
            plan = _build_agent_plan(_parse_model_json(text), max_tasks)
        except (ValueError, ValidationError) as error:
            raise api_failure(502, "The model returned an invalid plan. Try again.", error)

    logger.info(
        "Plan structuré : %d tâches, %d infos manquantes, statut=%s",
        len(plan.tasks),
        len(plan.missing_information),
        plan.status.value,
    )
    return plan