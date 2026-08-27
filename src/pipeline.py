"""ExtractionPipeline: Pipeline pattern (architecture-poc.md §2.2) tying preprocessing, balloon
detection, extraction, normalization, and Excel export into one ordered flow, mirroring the data
flow in architecture-poc.md §1.3 (steps 4-8).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.balloon_detector import BalloonDetector
from src.excel_writer import ExcelWriter
from src.extraction_orchestrator import ExtractionOrchestrator
from src.models import ExtractedBalloon, ExtractionResult, JobRecord
from src.preprocessor import DrawingPreprocessor, PageImage
from src.tolerance_normalizer import ToleranceNormalizer


@dataclass
class PipelineContext:
    job: JobRecord
    file_bytes: bytes
    content_type: str
    drawing_number: str | None = None
    revision: str | None = None
    template_id: str = "as9102-form3"
    pages: list[PageImage] = field(default_factory=list)
    balloons: list[ExtractedBalloon] = field(default_factory=list)
    balloon_count_detected: int = 0
    excel_bytes: bytes | None = None


class ExtractionPipeline:
    def __init__(
        self,
        preprocessor: DrawingPreprocessor,
        balloon_detector: BalloonDetector,
        orchestrator: ExtractionOrchestrator,
        normalizer: ToleranceNormalizer,
        excel_writer: ExcelWriter,
    ):
        self._preprocessor = preprocessor
        self._detector = balloon_detector
        self._orchestrator = orchestrator
        self._normalizer = normalizer
        self._excel_writer = excel_writer

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.pages = self._preprocessor.process(ctx.file_bytes, ctx.content_type)

        all_balloons: list[ExtractedBalloon] = []
        detected_total = 0
        for page in ctx.pages:
            candidates, layout = self._detector.detect(page)
            detected_total += len(candidates)
            all_balloons.extend(self._orchestrator.extract_page(page, layout, candidates))

        ctx.balloons = self._normalizer.normalize_all(all_balloons)
        ctx.balloon_count_detected = detected_total
        ctx.excel_bytes = self._excel_writer.write(
            drawing_number=ctx.drawing_number, revision=ctx.revision, balloons=ctx.balloons
        )
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
