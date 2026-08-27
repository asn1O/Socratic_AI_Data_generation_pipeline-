from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Model:
    provider: str
    api_key: str
    model_name: str
    url: str
    rpd: int
    rpd_left: int
    tpd: Optional[int]
    tpd_left: Optional[int]
    last_use: Optional[datetime]
    path_to_response_msg: str
    path_to_response_usage: str
    tier: int

    @property
    def ok_to_use(self) -> bool:
        """Check whether the model still has usable quota."""

        tpd_ok = (self.tpd_left is None) or (self.tpd_left > 0)
        return (self.rpd_left > 0) and tpd_ok

    def to_dict(self) -> dict:
        """Serialize Model to a JSON-serializable dictionary."""

        d = asdict(self)
        d["last_use"] = self.last_use.isoformat() if self.last_use else None
        return d

    @staticmethod
    def from_dict(d: dict) -> "Model":
        """Create a Model from a dictionary."""

        last = d.get("last_use")
        last_use = datetime.fromisoformat(last) if last else None

        raw_tpd = d.get("tpd")
        tpd = int(raw_tpd) if raw_tpd is not None else None

        raw_tpd_left = d.get("tpd_left")

        if raw_tpd_left is None:
            tpd_left = tpd
        else:
            tpd_left = int(raw_tpd_left)

        return Model(
            provider=d["provider"],
            api_key=d["api_key"],
            model_name=d["model_name"],
            url=d["url"],
            rpd=int(d["rpd"]),
            rpd_left=int(d.get("rpd_left", d["rpd"])),
            tpd=tpd,
            tpd_left=tpd_left,
            last_use=last_use,
            path_to_response_msg=d.get("path_to_response_msg", ""),
            path_to_response_usage=d.get("path_to_response_usage", ""),
            tier=int(d["tier"])
        )

    def display_details(self):
        print(f"Provider: {self.provider}")
        print(f"Model: {self.model_name}")
        print(f"URL: {self.url}")
        print(f"RPD: {self.rpd}")
        print(f"RPD Left: {self.rpd_left}")
        print(f"TPD: {self.tpd}")
        print(f"TPD Left: {self.tpd_left}")
        print(f"Last Use: {self.last_use}")
        print(f"Path to Response Msg: {self.path_to_response_msg}")
        print(f"Path to Response Usage: {self.path_to_response_usage}")
        print(f"Tier: {self.tier}")

    def test_connection(self) -> bool:
        """
        Test whether the model is reachable and returns usable text.

        Returns:
            True  -> request succeeded and model returned text
            False -> request failed or returned no usable text
        """

        success, _, _ = self.test_connection_with_reply()
        return success

    def test_connection_with_reply(self):
        """
        Test the model and return:

            (success, reply, total_tokens)

        Example:

            True, "yes"
            False, None, None
        """

        return self.call_model("Say only 'yes' or 'no'.")

    def test_socratic(self):
        """Generate a Socratic dataset prompt and send it to the model."""
        try:
            from .prompt_generator import DatasetPromptGenerator
        except ImportError:
            from prompt_generator import DatasetPromptGenerator

        prompt = DatasetPromptGenerator().generate_prompt()
        return self.call_model(prompt)

    def call_model(self, prompt: str):
        """
        Send a prompt to the model and return:

            (success, reply, total_tokens)
        """

        # Get provider hostname
        try:
            from urllib.parse import urlparse

            parsed = urlparse(self.url or "")
            provider = parsed.netloc or self.url

        except Exception:
            provider = self.url

        # Import OpenAI SDK
        try:
            from openai import (
                OpenAI,
                APIError,
                APIConnectionError,
                RateLimitError,
                AuthenticationError,
                APIStatusError,
            )

        except Exception:
            print(
                f"[provider={provider} model={self.model_name}] "
                "OpenAI SDK is not available. "
                "Install it with 'pip install openai'."
            )
            return False, None, 0

        try:
            # Create client
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.url if self.url else None,
            )

            # Make request
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                # max_tokens=20,
            )

            # Extract actual LLM reply
            #
            # Chat Completions response:
            #
            # response
            #   └── choices[0]
            #          └── message
            #                 └── content
            #
            reply = response.choices[0].message.content
            total_tokens = response.usage.total_tokens if response.usage else None

            # Model was reachable but didn't return text
            if not reply:
                print(
                    f"[provider={provider} model={self.model_name}] "
                    "Model was reachable but returned no text."
                )
                return False, None, total_tokens

            # Success
            print(
                f"[provider={provider} model={self.model_name}] "
                "Success: received response — model appears reachable."
            )

            return True, reply, total_tokens

        # -------------------------
        # Rate limit
        # -------------------------

        except RateLimitError:
            print(
                f"[provider={provider} model={self.model_name}] "
                "Rate limited (429). "
                "Please check your quota and try again later."
            )
            return False, None, None

        # -------------------------
        # Authentication
        # -------------------------

        except AuthenticationError:
            print(
                f"[provider={provider} model={self.model_name}] "
                "Authentication error (401). "
                "Check API key and permissions."
            )
            return False, None, None

        # -------------------------
        # HTTP status errors
        # -------------------------

        except APIStatusError as e:

            if e.status_code == 404:
                print(
                    f"[provider={provider} model={self.model_name}] "
                    f"Not found (404): model '{self.model_name}' "
                    "was not found. Check the model name."
                )
                return False, None, None

            print(
                f"[provider={provider} model={self.model_name}] "
                f"HTTP {e.status_code}. "
                f"Response: {str(e)[:500]}"
            )

            return False, None, None

        # -------------------------
        # Connection errors
        # -------------------------

        except APIConnectionError as e:
            print(
                f"[provider={provider} model={self.model_name}] "
                f"Connection error when contacting {self.url}: {e}"
            )
            return False, None, None

        # -------------------------
        # Other OpenAI API errors
        # -------------------------

        except APIError as e:
            print(
                f"[provider={provider} model={self.model_name}] "
                f"API error: {e}"
            )
            return False, None, None

        # -------------------------
        # Anything unexpected
        # -------------------------

        except Exception as e:
            print(
                f"[provider={provider} model={self.model_name}] "
                f"Unexpected error when attempting request: {e}"
            )
            return False, None, None