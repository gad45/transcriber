"""Quality control module for Hungarian transcription validation."""

import json
import os
from dataclasses import dataclass
from enum import Enum

from rich.console import Console

from .config import Config
from .transcriber import Segment

console = Console()

# Import Gemini
try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


class QCStatus(Enum):
    """Quality control status for a segment."""
    PASSED = "passed"
    WARNING = "warning"  # Minor issues, keep segment
    FAILED = "failed"    # Major issues, needs correction


@dataclass
class QCResult:
    """Result of quality control check for a segment."""
    segment: Segment
    status: QCStatus
    original_text: str
    corrected_text: str | None  # None if no correction needed
    issues: list[str]  # List of identified issues
    confidence_score: float  # 0.0-1.0 from LLM assessment


@dataclass
class QCReport:
    """Overall QC report for all segments."""
    results: list[QCResult]
    total_segments: int
    passed_count: int
    warning_count: int
    failed_count: int
    corrections_made: int


class QualityController:
    """
    Validates and optionally corrects Hungarian transcription segments.

    Uses Gemini 3 Flash to:
    1. Check semantic coherence (does the text make sense?)
    2. Validate Hungarian grammar
    3. Suggest corrections for errors
    """

    BATCH_SIZE = 10  # Process segments in batches to optimize API calls

    def __init__(self, config: Config, auto_correct: bool = True):
        """
        Initialize the quality controller.

        Args:
            config: Application configuration
            auto_correct: If True, automatically apply LLM corrections.
                         If False, only flag issues without modifying segments.
        """
        self.config = config
        self.auto_correct = auto_correct
        self._gemini_client = None
        self._model = config.qc_model

        # Initialize Gemini client
        gemini_key = os.getenv("GEMINI_API_KEY")
        if GEMINI_AVAILABLE and gemini_key:
            self._gemini_client = genai.Client(api_key=gemini_key)
            console.print(f"[green]✓[/green] Using {self._model} for transcription QC")
        else:
            if not GEMINI_AVAILABLE:
                console.print("[yellow]Warning: google-genai not installed - skipping QC[/yellow]")
            else:
                console.print("[yellow]Warning: GEMINI_API_KEY not set - skipping QC[/yellow]")

    def is_available(self) -> bool:
        """Check if QC is available (Gemini client initialized)."""
        return self._gemini_client is not None

    def check_segments(self, segments: list[Segment]) -> QCReport:
        """
        Run quality control on all segments.

        Args:
            segments: List of transcribed segments

        Returns:
            QCReport with results for all segments
        """
        if not self.is_available():
            return self._create_passthrough_report(segments)

        console.print(f"[blue]Running QC on {len(segments)} segments...[/blue]")

        results: list[QCResult] = []

        # Process in batches
        for i in range(0, len(segments), self.BATCH_SIZE):
            batch = segments[i:i + self.BATCH_SIZE]
            batch_results = self._check_batch(batch, i // self.BATCH_SIZE + 1)
            results.extend(batch_results)

        # Build report
        report = self._build_report(results)

        console.print(
            f"[green]✓[/green] QC complete: "
            f"{report.passed_count} passed, "
            f"{report.warning_count} warnings, "
            f"{report.failed_count} failed"
        )

        if report.corrections_made > 0:
            console.print(f"[yellow]Applied {report.corrections_made} corrections[/yellow]")

        return report

    def _check_batch(self, segments: list[Segment], batch_num: int) -> list[QCResult]:
        """Check a batch of segments with a single LLM call."""
        prompt = self._build_batch_prompt(segments)

        try:
            response = self._gemini_client.models.generate_content(
                model=self._model,
                contents=prompt
            )
            return self._parse_batch_response(segments, response.text)
        except Exception as e:
            console.print(f"[dim]QC batch {batch_num} skipped: {e}[/dim]")
            # Return all passed on error
            return [
                QCResult(
                    segment=seg,
                    status=QCStatus.PASSED,
                    original_text=seg.text,
                    corrected_text=None,
                    issues=[],
                    confidence_score=1.0
                )
                for seg in segments
            ]

    def _build_batch_prompt(self, segments: list[Segment]) -> str:
        """Build the LLM prompt for batch QC."""
        segments_list = []
        for i, seg in enumerate(segments):
            segments_list.append({
                "id": i + 1,
                "text": seg.text,
                "duration": f"{seg.duration:.1f}s"
            })

        segments_str = json.dumps(segments_list, ensure_ascii=False, indent=2)

        prompt = f"""You are a Hungarian language quality control expert. Your task is to validate transcribed speech segments.

IMPORTANT: The transcription is ALWAYS in Hungarian (Magyar nyelv). You must evaluate the text using Hungarian grammar rules, vocabulary, and semantics.

For each segment, evaluate:
1. SEMANTIC COHERENCE: Does the Hungarian text make logical sense? Is it a coherent thought or sentence fragment?
2. GRAMMAR: Is the Hungarian grammar correct? Check for:
   - Proper noun cases (nominative, accusative, dative, etc.)
   - Verb conjugations (definite/indefinite, person/number agreement)
   - Word order (topic-focus structure)
   - Proper use of suffixes and postpositions
3. TRANSCRIPTION ERRORS: Look for common ASR mistakes in Hungarian:
   - Misheard similar-sounding words
   - Incorrect word boundaries
   - Missing or extra words
   - Accent/diacritic errors (á/a, é/e, ö/o, ü/u, ő/ö, ű/ü, etc.)

Here are the segments to evaluate:
{segments_str}

Respond in JSON format with this structure:
{{
  "results": [
    {{
      "id": 1,
      "status": "passed" | "warning" | "failed",
      "confidence": 0.0-1.0,
      "issues": ["issue1", "issue2"],
      "correction": "corrected text" or null
    }}
  ]
}}

Status meanings:
- "passed": Text is correct Hungarian with no issues
- "warning": Minor issues (informal speech, slight awkwardness) but understandable
- "failed": Significant grammar errors, nonsensical text, or obvious transcription mistakes

Only provide a "correction" if:
1. The status is "warning" or "failed"
2. You are confident in the correction
3. The correction maintains the original meaning

Respond ONLY with the JSON, no additional text."""

        return prompt

    def _parse_batch_response(
        self,
        segments: list[Segment],
        response: str
    ) -> list[QCResult]:
        """Parse LLM response into QCResult objects."""
        results = []

        try:
            # Clean response - remove markdown code blocks if present
            response = response.strip()
            if response.startswith("```"):
                # Remove ```json and ``` markers
                lines = response.split("\n")
                response = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            data = json.loads(response)
            result_data = data.get("results", [])

            # Map results back to segments
            for seg_idx, seg in enumerate(segments):
                # Find matching result by id
                result_item = None
                for r in result_data:
                    if r.get("id") == seg_idx + 1:
                        result_item = r
                        break

                if result_item:
                    status_str = result_item.get("status", "passed")
                    status = QCStatus(status_str) if status_str in ["passed", "warning", "failed"] else QCStatus.PASSED

                    results.append(QCResult(
                        segment=seg,
                        status=status,
                        original_text=seg.text,
                        corrected_text=result_item.get("correction"),
                        issues=result_item.get("issues", []),
                        confidence_score=result_item.get("confidence", 1.0)
                    ))
                else:
                    # No result for this segment - mark as passed
                    results.append(QCResult(
                        segment=seg,
                        status=QCStatus.PASSED,
                        original_text=seg.text,
                        corrected_text=None,
                        issues=[],
                        confidence_score=1.0
                    ))

        except json.JSONDecodeError:
            # JSON parsing failed - return all passed
            for seg in segments:
                results.append(QCResult(
                    segment=seg,
                    status=QCStatus.PASSED,
                    original_text=seg.text,
                    corrected_text=None,
                    issues=[],
                    confidence_score=1.0
                ))

        return results

    def _build_report(self, results: list[QCResult]) -> QCReport:
        """Build the final QC report."""
        passed = sum(1 for r in results if r.status == QCStatus.PASSED)
        warnings = sum(1 for r in results if r.status == QCStatus.WARNING)
        failed = sum(1 for r in results if r.status == QCStatus.FAILED)
        corrections = sum(1 for r in results if r.corrected_text is not None)

        return QCReport(
            results=results,
            total_segments=len(results),
            passed_count=passed,
            warning_count=warnings,
            failed_count=failed,
            corrections_made=corrections
        )

    def _create_passthrough_report(self, segments: list[Segment]) -> QCReport:
        """Create a report that passes all segments (when QC unavailable)."""
        results = [
            QCResult(
                segment=seg,
                status=QCStatus.PASSED,
                original_text=seg.text,
                corrected_text=None,
                issues=[],
                confidence_score=1.0
            )
            for seg in segments
        ]
        return self._build_report(results)

    def apply_corrections(
        self,
        segments: list[Segment],
        report: QCReport
    ) -> list[Segment]:
        """
        Apply corrections from QC report to segments.

        Args:
            segments: Original segments
            report: QC report with corrections

        Returns:
            New list of segments with corrections applied
        """
        if not self.auto_correct:
            return segments

        corrected_segments = []

        # Build a map from segment id to result
        seg_to_result = {}
        for result in report.results:
            seg_to_result[id(result.segment)] = result

        for seg in segments:
            result = seg_to_result.get(id(seg))
            if result and result.corrected_text:
                # Create new segment with corrected text
                corrected_segments.append(Segment(
                    start=seg.start,
                    end=seg.end,
                    text=result.corrected_text,
                    confidence=seg.confidence,
                    tokens=seg.tokens
                ))
            else:
                corrected_segments.append(seg)

        return corrected_segments
