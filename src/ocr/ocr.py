import base64
import mimetypes
import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()

class OCR:
    def __init__(
        self,
        base_url: str = "https://backend.sovereigneg.com/v1",
        model: str = "qwen3.8-27b",
        api_key: str | None = None,
    ):
        self.model = model
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key or os.getenv("OPENAI_API_KEY", "EMPTY"),
        )

    def encode_image(
        self,
        image: bytes | str | Path,
        mime_type: str | None = None,
    ) -> tuple[str, str]:
        """Encodes an image (from bytes or file path) to a base64 string and detects/validates MIME type.

        Returns:
            tuple[str, str]: (base64_encoded_string, mime_type)
        """
        if isinstance(image, (str, Path)):
            path = Path(image)
            if not path.is_file():
                raise FileNotFoundError(f"Image file not found: {path}")
            if mime_type is None:
                mime_type, _ = mimetypes.guess_type(path)
            image_bytes = path.read_bytes()
        elif isinstance(image, bytes):
            image_bytes = image
        else:
            raise TypeError("Image must be bytes, str path, or Path object")

        detected_mime = mime_type or "image/jpeg"
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        return base64_image, detected_mime

    def extract_text(
        self,
        image: bytes | str | Path,
        mime_type: str | None = None,
    ) -> str:
        """Extracts text from an image using the vision model."""
        base64_image, detected_mime = self.encode_image(image, mime_type=mime_type)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "ocr and do nothing more. don't return any extra text"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{detected_mime};base64,{base64_image}",
                            },
                        },
                    ],
                },
            ],
        )
        return response.choices[0].message.content or ""

if __name__ == '__main__':
    ocr = OCR()

    res = ocr.extract_text('./test_images/sab.jpg')
    print("="*50)
    print(res)
    print("="*50)