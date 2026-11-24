import uvicorn

from .config import Settings
from .main import create_app


def main():
    settings = Settings.load()
    print(f"LabAgent: http://{settings.host}:{settings.port}")
    print("仅研究原型；设备/模型未配置时不会生成假识别或假测量。按 Ctrl+C 停止。")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
