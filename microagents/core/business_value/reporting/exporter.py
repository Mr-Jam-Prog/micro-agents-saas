import json
from pathlib import Path
from datetime import datetime

class ReportExporter:
    def __init__(self, output_dir=None, *args, **kwargs): self.output_dir = output_dir
    def generate_report(self, data=None, format="pdf", template=None, *args, **kwargs):
        class DateTimeEncoder(json.JSONEncoder):
            def default(self, o):
                if isinstance(o, datetime): return o.isoformat()
                return super().default(o)
        path = "/tmp/report.pdf"
        if self.output_dir:
            if format == "json":
                path = str(Path(self.output_dir) / "report.json")
            else:
                path = str(Path(self.output_dir) / "report.pdf")

        # Simuler la génération
        if format == "json":
            with open(path, "w") as f:
                json.dump(data if data else {"roi_analysis": {}, "forecast": []}, f, cls=DateTimeEncoder)
        else:
            with open(path, "w") as f:
                f.write("%PDF-1.4 simulated")

        return path
