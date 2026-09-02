"""ExtractionPipeline: Pipeline pattern (architecture-poc.md §2.2) tying preprocessing, balloon
detection, extraction, normalization, and title-block parsing into one ordered flow, mirroring the
data flow in architecture-poc.md §1.3 (steps 4-7). Excel generation is deliberately NOT part of
this pipeline -- it only happens at export time, after sign-off, from the reconciled values (see
app.py's export_drawing route and src/excel_writer.py); running it here too would just discard the
result unread on every extraction.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.balloon_detector import BalloonDetector
from src.extraction_orchestrator import ExtractionOrchestrator
from src.models import ExtractedBalloon, ExtractionResult, JobRecord
from src.preprocessor import DrawingPreprocessor, PageImage
from src.title_block_extractor import TitleBlockExtractor
from src.title_block_vision_reader import TitleBlockVisionReader
from src.tolerance_normalizer import ToleranceNormalizer


@dataclass
class PipelineContext:
    job: JobRecord
    file_bytes: bytes
    content_type: str
    # Filled in by run() from the title block (FR-02, src/title_block_extractor.py) unless already
    # set here -- a caller-supplied value (tests mostly) is treated as an override and left alone.
    drawing_number: str | None = None
    revision: str | None = None
    # Export template requested at upload time (src/excel_templates.py); None means "use the
    # registry's default" -- resolved lazily at export time, not here (see module docstring).
    template_id: str | None = None
    pages: list[PageImage] = field(default_factory=list)
    balloons: list[ExtractedBalloon] = field(default_factory=list)
    balloon_count_detected: int = 0


class ExtractionPipeline:
    def __init__(
        self,
        preprocessor: DrawingPreprocessor,
        balloon_detector: BalloonDetector,
        orchestrator: ExtractionOrchestrator,
        normalizer: ToleranceNormalizer,
        title_block_extractor: TitleBlockExtractor,
        title_block_vision_reader: TitleBlockVisionReader,
    ):
        self._preprocessor = preprocessor
        self._detector = balloon_detector
        self._orchestrator = orchestrator
        self._normalizer = normalizer
        self._title_block = title_block_extractor
        self._title_block_vision = title_block_vision_reader

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.pages = self._preprocessor.process(ctx.file_bytes, ctx.content_type)

        all_balloons: list[ExtractedBalloon] = []
        detected_total = 0
        for page in ctx.pages:
            candidates, layout = self._detector.detect(page)
            detected_total += len(candidates)
            all_balloons.extend(self._orchestrator.extract_page(page, layout, candidates))

            # Cheap: reuses the layout already fetched for balloon detection, no extra API call.
            # Stops looking once both fields are found -- the title block is conventionally on one
            # sheet, usually the first, and a caller-supplied value (see PipelineContext) is never
            # overwritten.
            if ctx.drawing_number is None or ctx.revision is None:
                fields = self._title_block.extract(layout)
                if ctx.drawing_number is None:
                    ctx.drawing_number = fields.drawing_number
                if ctx.revision is None:
                    ctx.revision = fields.revision

        # The regex heuristic didn't find everything -- fall back to asking the vision model to
        # read the title block directly off the first page's image (src/title_block_vision_reader.py).
        # One extra Claude call at most, only when needed, not per page.
        if (ctx.drawing_number is None or ctx.revision is None) and ctx.pages:
            vision_fields = self._title_block_vision.read(ctx.pages[0].png_bytes)
            if ctx.drawing_number is None:
                ctx.drawing_number = vision_fields.drawing_number
            if ctx.revision is None:
                ctx.revision = vision_fields.revision

        ctx.balloons = self._normalizer.normalize_all(all_balloons)
        ctx.balloon_count_detected = detected_total
        return ctx

    @staticmethod
    def to_result(ctx: PipelineContext, export_url: str | None) -> ExtractionResult:
        extracted_count = len([b for b in ctx.balloons if b.extraction_error is None])
        return ExtractionResult(
            job_id=ctx.job.job_id,
            drawing_number=ctx.drawing_number,
            revision=ctx.revision,
            balloon_count_detected=ctx.balloon_count_detected,
            balloon_count_extracted=extracted_count,
            balloon_count_mismatch=ctx.balloon_count_detected != extracted_count,
            balloons=ctx.balloons,
            export_url=export_url,
        )
