"""One cancellable background process for GUI capture and recognition.

No Qt imports. The process retains the optional model/embedding cache between tasks.
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path
from uuid import uuid4


def _serve(connection):
    recognizer = None
    configuration = None
    try:
        while True:
            task = connection.recv()
            try:
                if task["op"] == "propose":
                    from sticker_maker.recognition import OpenCVRecognizer
                    result = OpenCVRecognizer().propose(Path(task["image"]), work_area=task["work_area"])
                elif task["op"] == "refine":
                    from sticker_maker.sam import SAMRecognizer
                    if configuration != task["configuration"]:
                        recognizer = SAMRecognizer(**task["configuration"])
                        configuration = task["configuration"]
                    result = recognizer.refine(Path(task["image"]), box_px=task.get("box"),
                                               points=task.get("points", []), labels=task.get("labels", []),
                                               asset_dir=Path(task["asset_dir"]))
                elif task["op"] == "capture":
                    from sticker_maker.capture import capture_image
                    captured = capture_image(Path(task["output_dir"]), manual_focus=True)
                    result = {"image": str(captured.image_path), "metadata": captured.metadata}
                else:
                    raise ValueError("Unknown background task.")
                connection.send({"task": task, "result": result})
            except Exception as exc:
                connection.send({"task": task, "error": f"{type(exc).__name__}: {exc}"})
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


class BackgroundTasks:
    def __init__(self):
        self.process = self.connection = self.pending = None

    def submit(self, task: dict):
        if self.pending is not None:
            raise ValueError("Wait for the current task or cancel it first.")
        if self.process is None or not self.process.is_alive():
            self.close()
            context = multiprocessing.get_context("spawn")
            self.connection, child = context.Pipe()
            self.process = context.Process(target=_serve, args=(child,), daemon=True)
            self.process.start()
            child.close()
        self.pending = task | {"request_id": uuid4().hex}
        self.connection.send(self.pending)

    def poll(self):
        if self.pending is None:
            return None
        if self.connection.poll():
            try:
                result = self.connection.recv()
            except EOFError:
                result = {"task": self.pending, "error": "Background worker stopped unexpectedly; retry the task."}
            self.pending = None
            return result
        if not self.process.is_alive():
            result = {"task": self.pending, "error": "Background worker stopped unexpectedly; retry the task."}
            self.close()
            return result
        return None

    def close(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=1)
            self.process.close()
        if self.connection is not None:
            self.connection.close()
        self.process = self.connection = self.pending = None
