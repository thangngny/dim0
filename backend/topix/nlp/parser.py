"""Module containing the MistralParser class used to parse a PDF document using the Mistral OCR API."""

import base64
import logging
import os

from mistralai import Mistral, OCRPageObject
from pypdf import PdfReader

from topix.config.config import Config, MistralConfig
from topix.datatypes.mime import MimeTypeEnum

logger = logging.getLogger(__name__)


class MistralParser():
    """Parse a PDF document using the Mistral OCR API.

    Requires a Mistral API key; used when ``MISTRAL_API_KEY`` is configured so
    scanned/image PDFs get OCR'd (PypdfParser only extracts embedded text).
    Same ``.parse(filepath)`` return shape as PypdfParser so the pipeline is
    agnostic to which parser it holds.
    """

    def __init__(self, api_key: str | None = None):
        """Initialize the MistralParser with a Mistral API client."""
        if api_key is None:
            raise ValueError("API key is required, got None")
        self.client = Mistral(api_key=api_key)

    @classmethod
    def from_config(cls):
        """Create an instance of MistralParser from configuration."""
        config: Config = Config.instance()
        mistral_config: MistralConfig = config.run.apis.mistral

        return cls(api_key=mistral_config.api_key.get_secret_value() if mistral_config.api_key else None)

    def get_num_pages(self, fname: str) -> int:
        """Return the number of pages in a PDF file, or -1 on error."""
        try:
            with open(fname, "rb") as f:
                return len(PdfReader(f).pages)
        except Exception as e:  # noqa: BLE001
            logger.error("pypdf page count failed: %s", e)
            return -1

    def detect_mime_type(self, filepath: str) -> MimeTypeEnum:
        """Return the MIME type for a file path; only PDF is supported."""
        if os.path.splitext(filepath)[1].lower() == ".pdf":
            return MimeTypeEnum.PDF
        raise ValueError("Unsupported file format")

    def post_process_page(self, page: OCRPageObject) -> dict[str, int | str]:
        """Convert one Mistral OCR page into a ``{markdown, page}`` record."""
        return {
            "markdown": page.markdown,
            "page": page.index,
        }

    def encode_pdf(self, pdf_path: str) -> str:
        """Base64-encode a PDF file for the Mistral OCR document_url payload."""
        with open(pdf_path, "rb") as pdf_file:
            return base64.b64encode(pdf_file.read()).decode("utf-8")

    async def parse(
        self,
        filepath: str,
        max_pages: int = 200,
    ) -> list[dict[str, int | str]]:
        """Parse a PDF via the Mistral OCR API, returning per-page markdown.

        Asserts the file is a PDF and within ``max_pages``; raises on OCR
        failure.
        """
        assert self.detect_mime_type(filepath) == MimeTypeEnum.PDF, "Unsupported file format"
        assert self.get_num_pages(filepath) <= max_pages, (
            f"PDF file exceeds the maximum number of pages: {max_pages}"
        )

        res = await self.client.ocr.process_async(
            model="mistral-ocr-latest",
            document={
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{self.encode_pdf(filepath)}",
            },
            include_image_base64=False,
        )

        logger.info("Number of pages in the PDF file: %s", len(res.pages))

        return [self.post_process_page(page) for page in res.pages]


class PypdfParser:
    """Local PDF text extractor (no API key), fallback when Mistral OCR is off.

    Extracts embedded text per page with pypdf. Text-based PDFs parse fine;
    scanned/image-only PDFs fall back to vision OCR via the Ollama Cloud
    vision model, so they still work without a Mistral key.
    """

    def get_num_pages(self, fname: str) -> int:
        """Return the number of pages in a PDF file, or -1 on error."""
        try:
            with open(fname, "rb") as f:
                return len(PdfReader(f).pages)
        except Exception as e:  # noqa: BLE001
            logger.error("pypdf page count failed: %s", e)
            return -1

    def detect_mime_type(self, filepath: str) -> MimeTypeEnum:
        """Return the MIME type for a file path; only PDF is supported."""
        if os.path.splitext(filepath)[1].lower() == ".pdf":
            return MimeTypeEnum.PDF
        raise ValueError("Unsupported file format")

    async def parse(
        self,
        filepath: str,
        max_pages: int = 200,
    ) -> list[dict[str, int | str]]:
        """Extract per-page text from a PDF.

        Text-based pages use pypdf. Pages with little/no embedded text
        (scanned / image PDFs) fall back to vision OCR: the page is rendered
        to an image and transcribed by the Ollama Cloud vision model.
        """
        assert self.detect_mime_type(filepath) == MimeTypeEnum.PDF, "Unsupported file format"
        with open(filepath, "rb") as f:
            reader = PdfReader(f)
            pages: list[dict[str, int | str]] = []
            for i, page in enumerate(reader.pages[:max_pages]):
                text = (page.extract_text() or "").strip()
                # Thin text → likely a scanned/image page → vision OCR.
                if len(text) < 20:
                    ocr = await _vision_ocr_page(filepath, i)
                    if ocr:
                        text = ocr
                pages.append({"markdown": text, "page": i})
            return pages


async def _vision_ocr_page(filepath: str, page_index: int) -> str:
    """Render one PDF page to an image and transcribe it via the Ollama Cloud vision model."""
    try:
        import fitz  # pymupdf

        from topix.agents.notes.vision import _describe_image  # local import to avoid cycle
    except ImportError:
        return ""
    try:
        doc = fitz.open(filepath)
        if page_index >= len(doc):
            return ""
        pix = doc[page_index].get_pixmap(dpi=150)
        png_bytes = pix.tobytes("png")
        import base64 as _b64
        data_url = f"data:image/png;base64,{_b64.b64encode(png_bytes).decode('utf-8')}"
        doc.close()
        return await _describe_image(
            data_url,
            "Transcribe ALL text visible on this page exactly as written, "
            "preserving line breaks and structure. If there is no text, "
            "say '(no text)'.",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("vision OCR failed on page %s: %s", page_index, e)
        return ""


def get_parser():
    """Return a MistralParser if MISTRAL_API_KEY is set, else a PypdfParser fallback."""
    try:
        return MistralParser.from_config()
    except ValueError:
        logger.info("Mistral OCR key not set — falling back to pypdf text extraction (no OCR).")
        return PypdfParser()
