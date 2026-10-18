from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env (and any .env in the working directory) before anything reads os.environ.
# Real environment variables always win over the file.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv()
