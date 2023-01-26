from typing import Dict, Any, List, Union
import os
import json
import urllib.request, urllib.error

class Block:
    def to_json(self) -> dict[str, Any]:
        raise NotImplemented

class Accessory: pass

class Response:
    def __init__(self, text: str):
        self.text = text
        self.blocks : list[Block] = []

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "blocks": [block.to_json() for block in self.blocks],
        }

    def add(self, block : Block) -> None:
        assert isinstance(block, Block), "Can only add Blocks to Response"
        self.blocks.append(block)

class TextBlock(Block):
    def __init__(self, text):
        self.text = text
        self.accessory = None

    def to_json(self) -> dict[str, Any]:
        block = {
            "type": "section",
            "text": { "type": "mrkdwn", "text": self.text },
        }
        if self.accessory:
            block["accessory"] = self.accessory.to_json()
        return block

    def add(self, accessory: Accessory) -> None:
        assert isinstance(accessory, Accessory), "Can only add Accessories to Blocks"
        assert not self.accessory, "This Block already has an Accessory"
        self.accessory = accessory
        
class Fields(Block):
    def __init__(self, fields : Dict[str, str]):
        self.fields = fields

    def to_json(self) -> dict[str, Any]:
        fields = []
        for k, v in self.fields.items():
            fields.append({
                "type": "mrkdwn",
                "text": "*" + k.title() + "*",
            })
            fields.append({
                "type": "mrkdwn",
                "text": v,
            })

        block = {
            "type": "section",
            "fields": fields,
        }
        return block
        
class Image(Block):
    def __init__(self, text, url):
        self.text = text
        self.url = url

    def to_json(self) -> dict[str, Any]:
        return {
            "type": "image",
            "image_url": self.url,
            "alt_text": self.text,
        }

class Button(Accessory):
    key = "accessory"

    def __init__(self, text, url, style=None):
        assert style in { None, "primary", "danger" }
        self.text = text
        self.url = url
        self.style = style

    def to_json(self) -> dict[str, Any]:
        accessory = {
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": self.text,
            },
            "url": self.url,
        }
        if self.style:
            accessory["style"] = self.style
        return accessory

def build_runs(name : str, runs : Dict[str, Dict[str, str]], baseurl : str) -> Response:
    res = Response(f"Nightly data for {name}")
    for branch, info in runs.items():
        result = info["result"]
        time = info["time"]
        text = f"Branch `{branch}` of `{name}` was a {result} in {time}"
        if "emoji" in info:
            text += " " + info["emoji"]
        block = TextBlock(text)

        if "success" != result:
            file = os.path.basename(info["file"])
            url = baseurl + "logs/" + file
            btn = Button("Error log", url, style="primary")
            block.add(btn)
        elif "url" in info:
            btn = Button("View Report", info["url"])
            block.add(btn)

        res.add(block)

        fields = {
            k: v for k, v in info.items()
            if k not in ["url", "emoji", "result", "time", "img", "file"]
        }
        if fields:
            res.add(Fields(fields))

        if "img" in info:
            url, *alttext = info["img"].split(" ")
            text = " ".join(alttext) or f"Image for {name} branch {branch}"
            img = Image(text, url)
            res.add(img)
    return res
    
def build_fatal(name : str, text : str, baseurl : str) -> Response:
    res = Response(f"Fatal error running nightlies for {name}")
    res.add(TextBlock(text))
    return res

def send(runner, url: str, res: Response) -> None:
    payload = json.dumps(res.to_json()).encode("utf8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf8")

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            runner.log(2, f"Slack returned response {response.status} {response.reason}, because {response.read()}")
    except urllib.error.HTTPError as exc:
        reason = exc.read().decode('utf-8')
        runner.log(2, f"Slack error: {exc.code} {exc.reason}, because {reason}")
        runner.log(2, repr(payload))
