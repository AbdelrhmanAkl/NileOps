import base64
import mimetypes
import os
import re
from datetime import date
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from openai import OpenAI


from schemas import FrontIdData
from prompt import EXTRACT_FRONT_ID_DATA


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

    @staticmethod
    def verify_nid_with_dob(nid: int | str | None, dob: str | None) -> bool:
        """Verifies that the 14-digit Egyptian National ID encodes the matching birthdate (DOB).

        Egyptian National ID structure:
        - Digit 1: Century ('2' = 1900-1999, '3' = 2000-2099)
        - Digits 2-3: Year (YY)
        - Digits 4-5: Month (MM)
        - Digits 6-7: Day (DD)
        """
        if not nid or not dob:
            return False

        # Convert Arabic-Indic numerals to ASCII digits
        arabic_to_ascii = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
        nid_str = re.sub(r"\D", "", str(nid).translate(arabic_to_ascii))
        dob_str = str(dob).translate(arabic_to_ascii)

        if len(nid_str) != 14:
            return False

        century_digit = nid_str[0]
        if century_digit == "2":
            nid_year = 1900 + int(nid_str[1:3])
        elif century_digit == "3":
            nid_year = 2000 + int(nid_str[1:3])
        else:
            return False

        nid_month = int(nid_str[3:5])
        nid_day = int(nid_str[5:7])

        # Validate that the extracted month and day form a valid calendar date
        try:
            date(nid_year, nid_month, nid_day)
        except ValueError:
            return False

        # Extract digits from DOB (e.g. YYYY/MM/DD or DD/MM/YYYY)
        dob_numbers = [int(n) for n in re.findall(r"\d+", dob_str)]
        if len(dob_numbers) != 3:
            return False

        if dob_numbers[0] >= 1900:
            dob_year, dob_month, dob_day = dob_numbers[0], dob_numbers[1], dob_numbers[2]
        elif dob_numbers[2] >= 1900:
            dob_day, dob_month, dob_year = dob_numbers[0], dob_numbers[1], dob_numbers[2]
        else:
            return False

        return nid_year == dob_year and nid_month == dob_month and nid_day == dob_day

    def extract_and_verify_front_id(
        self,
        image: bytes | str | Path,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        """Extracts information from the front side of the national ID card and verifies the NID against DOB.

        Returns:
            dict: {
                "verified": bool,
                "data": dict
            }
        """
        extracted_text = self.extract_text(image, mime_type)
        data: dict[str, Any] = {}

        # 1. Attempt using OpenAI structured output parser
        try:
            completion = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": EXTRACT_FRONT_ID_DATA},
                    {"role": "user", "content": f"OCR Extracted Text:\n{extracted_text}"},
                ],
                response_format=FrontIdData,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is not None:
                data = parsed.model_dump()
        except Exception:
            pass

        # 2. Fallback to standard chat completions if beta.parse fails
        if not data:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": EXTRACT_FRONT_ID_DATA},
                        {"role": "user", "content": f"OCR Extracted Text:\n{extracted_text}"},
                    ],
                    response_format={"type": "json_object"},
                )
                raw_content = response.choices[0].message.content or "{}"
            except Exception:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": EXTRACT_FRONT_ID_DATA},
                        {"role": "user", "content": f"OCR Extracted Text:\n{extracted_text}"},
                    ],
                )
                raw_content = response.choices[0].message.content or "{}"

            cleaned = raw_content.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines).strip()

            try:
                data = FrontIdData.model_validate_json(cleaned).model_dump()
            except Exception:
                import json
                try:
                    data = json.loads(cleaned)
                except Exception:
                    data = {"raw_text": extracted_text}

        verified = self.verify_nid_with_dob(data.get("nid"), data.get("dob"))

        return {
            "verified": verified,
            "data": data,
        }


if __name__ == '__main__':
    ocr = OCR()

    res = ocr.extract_and_verify_front_id('./test_images/sab.jpg')
    print("=" * 50)
    print(res)
    print("=" * 50)
