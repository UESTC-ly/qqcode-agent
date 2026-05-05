from tools.base import BaseTool  # This import must be present
import requests  # Add any other required imports
import os
from pathlib import Path
from datetime import datetime, timezone
import json


class CwdenvironmentTool(BaseTool):  # Class name must match name property in uppercase first letter
    name = "cwdenvironmenttool"  # Must match class name in lowercase
    description = '''
    Returns the current process working directory as an absolute path.
    Produces a simple JSON-like response containing the cwd and an optional UTC timestamp.
    Safe, cross-platform, and useful for development and environment awareness workflows.
    '''
    input_schema = {
        "type": "object",
        "properties": {
            "include_timestamp": {
                "type": "boolean",
                "description": "Whether to include an ISO-8601 UTC timestamp in the response."
            }
        },
        "required": []
    }

    def execute(self, **kwargs) -> str:
        include_timestamp = bool(kwargs.get("include_timestamp", False))
        cwd = str(Path(os.getcwd()).resolve())

        result = {"cwd": cwd}
        if include_timestamp:
            result["timestamp"] = datetime.now(timezone.utc).isoformat()

        return json.dumps(result)