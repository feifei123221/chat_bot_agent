import json
import mimetypes
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def load_local_env() -> None:
    """Load simple KEY=VALUE entries from the local .env file."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path, encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("\"'")
            os.environ.setdefault(key.strip(), value)


def request_model(messages: list[dict[str, str]]) -> str:
    """Send chat messages to the configured OpenAI-compatible API."""
    load_local_env()
    api_key = os.getenv("AI_API_KEY")
    base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("AI_MODEL", "gpt-4o-mini")

    if not api_key:
        raise RuntimeError(
            "未设置 AI_API_KEY。请先设置环境变量，例如：$env:AI_API_KEY='你的密钥'"
        )

    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
        }
    ).encode("utf-8")
    request = Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"模型接口返回错误 {error.code}: {details}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接模型接口: {error.reason}") from error

    try:
        return result["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(f"模型返回了无法识别的数据: {result}") from error


def transcribe_audio(audio_bytes: bytes, filename: str = "audio.wav") -> str:
    """Transcribe recorded audio through the configured OpenAI-compatible API."""
    load_local_env()
    api_key = os.getenv("AI_TRANSCRIPTION_API_KEY") or os.getenv("AI_API_KEY")
    base_url = os.getenv("AI_TRANSCRIPTION_BASE_URL")
    if not base_url:
        base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
    base_url = base_url.rstrip("/")
    model = os.getenv("AI_TRANSCRIPTION_MODEL", "whisper-1")

    if not api_key:
        raise RuntimeError(
            "未设置 AI_API_KEY。请先设置环境变量，例如：$env:AI_API_KEY='你的密钥'"
        )
    if not audio_bytes:
        raise RuntimeError("没有获取到有效的录音内容。")
    if "deepseek.com" in base_url.lower() and not os.getenv("AI_TRANSCRIPTION_BASE_URL"):
        raise RuntimeError(
            "当前对话模型是 DeepSeek，但它不提供语音转写接口。"
            "请配置 AI_TRANSCRIPTION_BASE_URL 和 AI_TRANSCRIPTION_API_KEY，"
            "或暂时使用文字输入。"
        )

    boundary = f"----ChatBotBoundary{uuid.uuid4().hex}".encode("ascii")
    content_type = mimetypes.guess_type(filename)[0] or "audio/wav"
    parts = [
        b"--" + boundary,
        b'Content-Disposition: form-data; name="model"',
        b"",
        model.encode("utf-8"),
        b"--" + boundary,
        (
            f'Content-Disposition: form-data; name="file"; filename="{filename}"'
        ).encode("utf-8"),
        f"Content-Type: {content_type}".encode("ascii"),
        b"",
        audio_bytes,
        b"--" + boundary + b"--",
        b"",
    ]
    payload = b"\r\n".join(parts)
    request = Request(
        f"{base_url}/audio/transcriptions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary.decode('ascii')}",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"语音转写接口返回错误 {error.code}: {details}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接语音转写接口: {error.reason}") from error

    try:
        text = result["text"].strip()
    except (KeyError, TypeError, AttributeError) as error:
        raise RuntimeError(f"语音转写接口返回了无法识别的数据: {result}") from error
    if not text:
        raise RuntimeError("语音转写结果为空，请重新录音。")
    return text
