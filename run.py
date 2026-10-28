#!/usr/bin/env python3
import argparse
from pathlib import Path
import os


def main():
    parser = argparse.ArgumentParser(description="本地视频阶段标注")
    parser.add_argument("--host", default="127.0.0.1", help="局域网访问时使用 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--data-dir", type=Path, help="本地视频和数据库保存目录")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["LOCAL_ANNOTATION_DATA_DIR"] = str(args.data_dir.expanduser().resolve())
    import uvicorn
    from local_annotation.app import create_app
    uvicorn.run(create_app(), host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
