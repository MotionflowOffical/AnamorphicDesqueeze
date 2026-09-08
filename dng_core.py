from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional


DNG_EXTENSIONS = {".dng"}
SWAPPED_ORIENTATIONS = {5, 6, 7, 8}


@dataclass(slots=True)
class DngInfo:
    source: Path
    raw_width: int
    raw_height: int
    orientation: int = 1
    default_scale_x: float = 1.0
    default_scale_y: float = 1.0
    raw_image_digest: str = ""
    raw_ifd_group: str = "IFD0"

    @property
    def swaps_axes(self) -> bool:
        return self.orientation in SWAPPED_ORIENTATIONS

    @property
    def display_width(self) -> int:
        return self.raw_height if self.swaps_axes else self.raw_width

    @property
    def display_height(self) -> int:
        return self.raw_width if self.swaps_axes else self.raw_height

    @property
    def display_orientation(self) -> str:
        if self.display_width > self.display_height:
            return "landscape"
        if self.display_height > self.display_width:
            return "portrait"
        return "square"

    def raw_axis_for_display_stretch(self) -> str:
        """Stretch long/display-natural anamorphic axis.

        Landscape photos de-squeeze along displayed X.
        Portrait photos de-squeeze along displayed Y.
        A 90/270-degree EXIF orientation swaps display and raw axes.
        """
        display_axis = "x" if self.display_orientation != "portrait" else "y"
        if self.swaps_axes:
            return "y" if display_axis == "x" else "x"
        return display_axis

    def target_default_scale(self, ratio: float, preserve_existing: bool = True) -> tuple[float, float]:
        if ratio <= 0 or not math.isfinite(ratio):
            raise ValueError("Anamorphic ratio must be a finite number greater than zero.")
        x = self.default_scale_x if preserve_existing else 1.0
        y = self.default_scale_y if preserve_existing else 1.0
        axis = self.raw_axis_for_display_stretch()
        if axis == "x":
            x *= ratio
        else:
            y *= ratio
        return x, y


@dataclass(slots=True)
class Job:
    info: DngInfo
    output: Path
    scale_x: float
    scale_y: float


@dataclass(slots=True)
class JobResult:
    source: Path
    output: Path
    ok: bool
    message: str = ""
    elapsed_s: float = 0.0


@dataclass(slots=True)
class BatchSummary:
    total: int = 0
    succeeded: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_written: int = 0
    elapsed_s: float = 0.0
    results: list[JobResult] = field(default_factory=list)


class CancelledError(RuntimeError):
    pass


def fmt_float(value: float) -> str:
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text or "0"


def parse_pair(value, default=(1.0, 1.0)) -> tuple[float, float]:
    if value is None:
        return default
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return default
    text = str(value).strip()
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)
    if len(nums) >= 2:
        try:
            return float(nums[0]), float(nums[1])
        except ValueError:
            pass
    return default


RAW_PHOTOMETRIC_CODES = {32803, 34892}  # CFA / LinearRaw
RAW_IFD_GROUP_RE = re.compile(r"^(?:IFD0|SubIFD\d*)$", re.IGNORECASE)


def _group_and_tag(key: str) -> tuple[str, str]:
    parts = str(key).split(":")
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "", parts[-1]


def _group_values(obj: dict, tag: str) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in obj.items():
        group, name = _group_and_tag(key)
        if name == tag and group:
            out[group] = value
    return out


def _first_group_value(obj: dict, tag: str, preferred: Iterable[str] = ()):
    values = _group_values(obj, tag)
    for group in preferred:
        if group in values:
            return values[group]
    return next(iter(values.values()), None)


def _numeric_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        nums = re.findall(r"-?\d+", str(value))
        return int(nums[0]) if nums else default


def _group_area(obj: dict, group: str) -> int:
    crop = _group_values(obj, "DefaultCropSize").get(group)
    if crop is not None:
        w, h = parse_pair(crop, (0.0, 0.0))
        if w > 0 and h > 0:
            return int(w * h)
    widths = _group_values(obj, "ImageWidth")
    heights = _group_values(obj, "ImageHeight")
    try:
        w, h = int(widths.get(group, 0)), int(heights.get(group, 0))
        return max(0, w) * max(0, h)
    except (TypeError, ValueError):
        return 0


def infer_raw_ifd_group(obj: dict) -> str:
    """Return the IFD which actually describes the RAW/LinearRaw image.

    DNGs are not uniform: some keep the sensor image in IFD0, others use a
    SubIFD and reserve IFD0 for a thumbnail.  DNG image tags such as
    DefaultScale belong to the IFD of the image they describe.
    """
    groups: set[str] = set()
    for key in obj:
        group, _ = _group_and_tag(key)
        if RAW_IFD_GROUP_RE.match(group):
            groups.add(group)
    if not groups:
        return "IFD0"

    photometric = _group_values(obj, "PhotometricInterpretation")
    cfa = _group_values(obj, "CFARepeatPatternDim")
    scales = _group_values(obj, "DefaultScale")

    def score(group: str) -> tuple[int, int]:
        points = 0
        if _numeric_int(photometric.get(group)) in RAW_PHOTOMETRIC_CODES:
            points += 1000
        if group in cfa:
            points += 500
        if group in scales:
            points += 100
        # Prefer the largest image when structural signals tie.
        return points, _group_area(obj, group)

    return max(groups, key=score)


def parse_size_for_group(obj: dict, group: str) -> tuple[int, int]:
    crop = _group_values(obj, "DefaultCropSize").get(group)
    if crop is not None:
        w, h = parse_pair(crop, (0.0, 0.0))
        if w > 0 and h > 0:
            return int(round(w)), int(round(h))
    widths = _group_values(obj, "ImageWidth")
    heights = _group_values(obj, "ImageHeight")
    try:
        w, h = int(widths.get(group, 0)), int(heights.get(group, 0))
        if w > 0 and h > 0:
            return w, h
    except (TypeError, ValueError):
        pass

    # Composite RawImageFullSize is useful when the image IFD itself omits a
    # convenient size pair in ExifTool's JSON output.
    full = _first_group_value(obj, "RawImageFullSize")
    if full:
        nums = re.findall(r"\d+", str(full))
        if len(nums) >= 2:
            return int(nums[0]), int(nums[1])
    return parse_size({k.split(":")[-1]: v for k, v in obj.items()})


def parse_size(obj: dict) -> tuple[int, int]:
    # Prefer DefaultCropSize because it describes the useful RAW image area.
    crop = obj.get("DefaultCropSize")
    if crop is not None:
        vals = parse_pair(crop, default=(0.0, 0.0))
        if vals[0] > 0 and vals[1] > 0:
            return int(round(vals[0])), int(round(vals[1]))

    candidates = [
        (obj.get("ImageWidth"), obj.get("ImageHeight")),
        (obj.get("ExifImageWidth"), obj.get("ExifImageHeight")),
    ]
    for w, h in candidates:
        try:
            wi, hi = int(w), int(h)
            if wi > 0 and hi > 0:
                return wi, hi
        except (TypeError, ValueError):
            continue

    full = obj.get("RawImageFullSize")
    if full:
        nums = re.findall(r"\d+", str(full))
        if len(nums) >= 2:
            return int(nums[0]), int(nums[1])
    raise ValueError("Could not determine DNG dimensions")


def scan_dng_paths(source_dir: Path, recursive: bool) -> list[Path]:
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source folder does not exist: {source_dir}")
    iterator = source_dir.rglob("*") if recursive else source_dir.glob("*")
    return sorted((p for p in iterator if p.is_file() and p.suffix.lower() in DNG_EXTENSIONS), key=lambda p: str(p).lower())


def locate_exiftool(app_dir: Optional[Path] = None) -> Optional[Path]:
    app_dir = (app_dir or Path(__file__).resolve().parent).resolve()
    candidates = [
        app_dir / "tools" / "exiftool.exe",
        app_dir / "tools" / "exiftool",
        app_dir / "exiftool.exe",
        app_dir / "exiftool",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which("exiftool") or shutil.which("exiftool.exe")
    return Path(found) if found else None


EXIFTOOL_USER_AGENT = "AnamorphicDNGBatch/1.0.3 (Windows; ExifTool bootstrapper)"


def _exiftool_version_urls() -> list[str]:
    # Phil Harvey documents ver.txt as the canonical current-version endpoint.
    # Keep a SourceForge-hosted fallback because ExifTool distribution hosting has
    # moved between exiftool.org and SourceForge over time.
    return [
        "https://exiftool.org/ver.txt",
        "https://exiftool.sourceforge.net/ver.txt",
    ]


def _exiftool_windows_download_urls(version: str) -> list[str]:
    filename = f"exiftool-{version}_64.zip"
    # Current official distribution is hosted on SourceForge.  The first URL is
    # the stable direct-download endpoint used by package managers such as Scoop.
    return [
        f"https://download.sourceforge.net/project/exiftool/files/{filename}",
        f"https://sourceforge.net/projects/exiftool/files/{filename}/download",
    ]


def _read_url_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": EXIFTOOL_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("ascii", "replace").strip()


def _download_first_valid_zip(urls: list[str], destination: Path, status: Callable[[str], None]) -> str:
    failures: list[str] = []
    for index, url in enumerate(urls, start=1):
        try:
            status(f"Downloading ExifTool from official mirror {index}/{len(urls)}…")
            req = urllib.request.Request(url, headers={"User-Agent": EXIFTOOL_USER_AGENT})
            with urllib.request.urlopen(req, timeout=180) as r, destination.open("wb") as f:
                shutil.copyfileobj(r, f, length=1024 * 1024)
            if not zipfile.is_zipfile(destination):
                raise RuntimeError("server response was not a ZIP archive")
            return url
        except Exception as exc:
            failures.append(f"{url}: {exc}")
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
    detail = "\n".join(failures)
    raise RuntimeError(
        "Could not download the official ExifTool Windows package from its mirrors. "
        "You can manually download the current 64-bit ExifTool ZIP from "
        "https://sourceforge.net/projects/exiftool/files/ and place exiftool.exe plus "
        "the exiftool_files folder inside this app's tools folder.\n\n" + detail
    )


def download_exiftool_windows(app_dir: Optional[Path] = None, status: Optional[Callable[[str], None]] = None) -> Path:
    if os.name != "nt":
        raise RuntimeError("Automatic ExifTool download is currently provided for Windows only. Install exiftool with your package manager.")
    app_dir = (app_dir or Path(__file__).resolve().parent).resolve()
    tools = app_dir / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    status = status or (lambda _: None)

    status("Checking current ExifTool version…")
    version = ""
    version_errors: list[str] = []
    for version_url in _exiftool_version_urls():
        try:
            candidate = _read_url_text(version_url)
            if re.fullmatch(r"\d+(?:\.\d+)+", candidate):
                version = candidate
                break
            version_errors.append(f"{version_url}: unexpected response {candidate!r}")
        except Exception as exc:
            version_errors.append(f"{version_url}: {exc}")
    if not version:
        raise RuntimeError("Could not determine the current ExifTool version.\n" + "\n".join(version_errors))

    status(f"Preparing ExifTool {version}…")
    with tempfile.TemporaryDirectory(prefix="anamorphic_dng_") as td:
        td_path = Path(td)
        archive = td_path / "exiftool.zip"
        _download_first_valid_zip(_exiftool_windows_download_urls(version), archive, status)

        unpacked = td_path / "unpacked"
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(unpacked)
        exe_candidates = list(unpacked.rglob("exiftool(-k).exe")) + list(unpacked.rglob("exiftool.exe"))
        if not exe_candidates:
            raise RuntimeError("Downloaded ExifTool archive did not contain the Windows executable.")
        exe = exe_candidates[0]
        files_dirs = list(unpacked.rglob("exiftool_files"))
        if not files_dirs:
            raise RuntimeError("Downloaded ExifTool archive did not contain the required exiftool_files folder.")

        target_exe = tools / "exiftool.exe"
        target_files = tools / "exiftool_files"
        staged_exe = tools / "exiftool.exe.new"
        staged_files = tools / "exiftool_files.new"
        if staged_exe.exists():
            staged_exe.unlink()
        if staged_files.exists():
            shutil.rmtree(staged_files)
        shutil.copy2(exe, staged_exe)
        shutil.copytree(files_dirs[0], staged_files)

        # Install only after both required components have been staged successfully.
        if target_exe.exists():
            target_exe.unlink()
        if target_files.exists():
            shutil.rmtree(target_files)
        staged_exe.replace(target_exe)
        staged_files.replace(target_files)

    status(f"ExifTool {version} installed.")
    return target_exe


def exiftool_metadata(exiftool: Path, paths: list[Path]) -> list[DngInfo]:
    if not paths:
        return []
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", suffix=".args", delete=False) as af:
        argfile = Path(af.name)
        for p in paths:
            af.write(str(p) + "\n")
    try:
        cmd = [
            str(exiftool),
            "-j", "-n", "-G1", "-a",
            "-FileName", "-Directory",
            "-ImageWidth", "-ImageHeight", "-ExifImageWidth", "-ExifImageHeight",
            "-RawImageFullSize", "-DefaultCropSize", "-Orientation", "-DefaultScale", "-RawImageDigest",
            "-PhotometricInterpretation", "-CFARepeatPatternDim",
            "-@", str(argfile),
        ]
        cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
        if cp.returncode not in (0, 1):
            raise RuntimeError(f"ExifTool metadata scan failed ({cp.returncode}): {cp.stderr.strip()}")
        try:
            data = json.loads(cp.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"ExifTool returned invalid JSON: {e}") from e

        by_norm = {os.path.normcase(os.path.abspath(str(p))): p for p in paths}
        infos: list[DngInfo] = []
        for obj in data:
            src_text = obj.get("SourceFile") or obj.get("System:FileName") or obj.get("File:FileName")
            if not src_text:
                continue
            src_abs = os.path.normcase(os.path.abspath(src_text))
            source = by_norm.get(src_abs, Path(src_text))
            raw_group = infer_raw_ifd_group(obj)
            w, h = parse_size_for_group(obj, raw_group)
            orientation_value = _first_group_value(obj, "Orientation", ("IFD0", raw_group))
            try:
                orientation = int(orientation_value or 1)
            except (TypeError, ValueError):
                orientation = 1
            scale_value = _group_values(obj, "DefaultScale").get(raw_group)
            if scale_value is None:
                scale_value = _first_group_value(obj, "DefaultScale", (raw_group, "IFD0"))
            sx, sy = parse_pair(scale_value, (1.0, 1.0))
            digest_value = _first_group_value(obj, "RawImageDigest", (raw_group, "IFD0"))
            infos.append(DngInfo(
                source=Path(source),
                raw_width=w,
                raw_height=h,
                orientation=orientation if 1 <= orientation <= 8 else 1,
                default_scale_x=sx,
                default_scale_y=sy,
                raw_image_digest=str(digest_value or ""),
                raw_ifd_group=raw_group,
            ))
        return infos
    finally:
        try:
            argfile.unlink()
        except OSError:
            pass


def build_jobs(
    infos: Iterable[DngInfo],
    source_root: Path,
    output_root: Path,
    ratio: float,
    recursive: bool,
    preserve_existing_scale: bool,
) -> list[Job]:
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    jobs: list[Job] = []
    for info in infos:
        try:
            rel = info.source.resolve().relative_to(source_root)
        except ValueError:
            rel = Path(info.source.name)
        if not recursive:
            rel = Path(rel.name)
        out = output_root / rel
        x, y = info.target_default_scale(ratio, preserve_existing_scale)
        jobs.append(Job(info=info, output=out, scale_x=x, scale_y=y))
    return jobs


class ExifToolSession:
    def __init__(self, executable: Path):
        self.executable = executable
        self.proc: Optional[subprocess.Popen[str]] = None
        self.counter = 0

    def __enter__(self):
        flags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            [str(self.executable), "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=flags,
        )
        return self

    def run(self, args: list[str]) -> tuple[bool, str]:
        if not self.proc or not self.proc.stdin or not self.proc.stdout:
            raise RuntimeError("ExifTool session is not running")
        self.counter += 1
        marker = str(self.counter)
        payload = "".join(arg + "\n" for arg in args) + f"-execute{marker}\n"
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()
        output_lines: list[str] = []
        ready = f"{{ready{marker}}}"
        while True:
            line = self.proc.stdout.readline()
            if line == "" and self.proc.poll() is not None:
                err = ""
                if self.proc.stderr:
                    err = self.proc.stderr.read()
                return False, f"ExifTool exited unexpectedly. {err.strip()}"
            if line.rstrip("\r\n") == ready:
                break
            output_lines.append(line.rstrip("\r\n"))
        text = "\n".join(output_lines).strip()
        lower = text.lower()
        # ExifTool commonly returns 0 even when a single file emits an Error line.
        ok = (
            "error:" not in lower
            and "nothing changed" not in lower
            and "0 image files updated" not in lower
            and "0 output files created" not in lower
        )
        return ok, text

    def __exit__(self, exc_type, exc, tb):
        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write("-stay_open\nFalse\n")
                self.proc.stdin.flush()
            except Exception:
                pass
        if self.proc:
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return False


def process_batch(
    exiftool: Path,
    jobs: list[Job],
    workers: int = 2,
    collision: str = "skip",
    cancel_event: Optional[threading.Event] = None,
    progress: Optional[Callable[[int, int, JobResult], None]] = None,
) -> BatchSummary:
    """Process output files with persistent ExifTool workers.

    ExifTool creates the output DNG directly while changing only metadata, so the
    app does not perform a preliminary full-file copy. This avoids doubling disk I/O.
    """
    start = time.perf_counter()
    cancel_event = cancel_event or threading.Event()
    progress = progress or (lambda done, total, result: None)
    summary = BatchSummary(total=len(jobs))
    q: queue.Queue[Optional[Job]] = queue.Queue()
    result_q: queue.Queue[JobResult] = queue.Queue()

    for job in jobs:
        q.put(job)
    worker_count = max(1, min(int(workers), 8, max(1, len(jobs))))
    for _ in range(worker_count):
        q.put(None)

    def worker_main():
        with ExifToolSession(exiftool) as session:
            while True:
                job = q.get()
                if job is None:
                    q.task_done()
                    break
                t0 = time.perf_counter()
                if cancel_event.is_set():
                    result_q.put(JobResult(job.info.source, job.output, False, "Cancelled", time.perf_counter() - t0))
                    q.task_done()
                    continue
                try:
                    job.output.parent.mkdir(parents=True, exist_ok=True)
                    dest = job.output
                    if dest.exists():
                        if collision == "skip":
                            result_q.put(JobResult(job.info.source, dest, True, "Skipped (already exists)", time.perf_counter() - t0))
                            q.task_done()
                            continue
                        if collision == "overwrite":
                            dest.unlink()
                        else:
                            stem, suffix = dest.stem, dest.suffix
                            idx = 1
                            while dest.exists():
                                dest = dest.with_name(f"{stem}_{idx}{suffix}")
                                idx += 1
                    part = dest.with_name(dest.stem + ".partial" + dest.suffix)
                    if part.exists():
                        part.unlink()
                    args = [
                        "-P",
                        f"-{job.info.raw_ifd_group}:DefaultScale={fmt_float(job.scale_x)} {fmt_float(job.scale_y)}",
                        "-o", str(part),
                        str(job.info.source),
                    ]
                    ok, text = session.run(args)
                    if ok and part.exists() and part.stat().st_size > 0:
                        os.replace(part, dest)
                        try:
                            shutil.copystat(job.info.source, dest, follow_symlinks=True)
                        except OSError:
                            pass
                        msg = (f"{job.info.raw_ifd_group}:DefaultScale="
                               f"{fmt_float(job.scale_x)} {fmt_float(job.scale_y)}")
                        result_q.put(JobResult(job.info.source, dest, True, msg, time.perf_counter() - t0))
                    else:
                        try:
                            part.unlink()
                        except OSError:
                            pass
                        result_q.put(JobResult(job.info.source, dest, False, text or "ExifTool did not create an output file", time.perf_counter() - t0))
                except Exception as e:
                    result_q.put(JobResult(job.info.source, job.output, False, str(e), time.perf_counter() - t0))
                finally:
                    q.task_done()

    threads = [threading.Thread(target=worker_main, name=f"dng-worker-{i+1}", daemon=True) for i in range(worker_count)]
    for t in threads:
        t.start()

    done = 0
    while done < len(jobs):
        result = result_q.get()
        summary.results.append(result)
        done += 1
        if result.ok and result.message.startswith("Skipped"):
            summary.skipped += 1
        elif result.ok:
            summary.succeeded += 1
            try:
                summary.bytes_written += result.output.stat().st_size
            except OSError:
                pass
        else:
            summary.failed += 1
        progress(done, len(jobs), result)

    q.join()
    for t in threads:
        t.join(timeout=1)
    summary.elapsed_s = time.perf_counter() - start
    return summary


def verify_outputs(exiftool: Path, jobs: list[Job], results: list[JobResult]) -> list[str]:
    """Fast metadata verification, including RawImageDigest when available."""
    successful = [r for r in results if r.ok and not r.message.startswith("Skipped") and r.output.exists()]
    if not successful:
        return []
    output_paths = [r.output for r in successful]
    output_infos = exiftool_metadata(exiftool, output_paths)
    by_out = {os.path.normcase(os.path.abspath(str(i.source))): i for i in output_infos}
    job_by_source = {os.path.normcase(os.path.abspath(str(j.info.source))): j for j in jobs}
    result_by_output = {os.path.normcase(os.path.abspath(str(r.output))): r for r in successful}
    issues: list[str] = []
    for out_key, result in result_by_output.items():
        info = by_out.get(out_key)
        if not info:
            issues.append(f"Could not verify metadata: {result.output}")
            continue
        job = job_by_source.get(os.path.normcase(os.path.abspath(str(result.source))))
        if not job:
            continue
        if info.raw_ifd_group != job.info.raw_ifd_group:
            issues.append(
                f"RAW IFD changed: {result.output.name} expected {job.info.raw_ifd_group}, got {info.raw_ifd_group}"
            )
        if not (math.isclose(info.default_scale_x, job.scale_x, rel_tol=1e-6, abs_tol=1e-6) and math.isclose(info.default_scale_y, job.scale_y, rel_tol=1e-6, abs_tol=1e-6)):
            issues.append(
                f"DefaultScale mismatch in {info.raw_ifd_group}: {result.output.name} "
                f"expected {job.scale_x:g} {job.scale_y:g}, got {info.default_scale_x:g} {info.default_scale_y:g}"
            )
        src_digest = job.info.raw_image_digest.strip().lower()
        out_digest = info.raw_image_digest.strip().lower()
        if src_digest and out_digest and src_digest != out_digest:
            issues.append(f"RAW image digest changed: {result.output.name}")
    return issues
