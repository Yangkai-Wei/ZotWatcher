"""工具函数"""
import os
import yaml
import logging
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv


def setup_logging(level=logging.INFO):
    """设置日志"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    return logging.getLogger("ZotWatcher")


def load_config() -> Dict[str, Any]:
    """加载配置文件"""
    # 加载环境变量
    load_dotenv()
    
    config_dir = Path("config")
    config = {}
    
    # 加载各配置文件
    for config_file in ["zotero.yaml", "sources.yaml", "scoring.yaml"]:
        config_path = config_dir / config_file
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
                # 替换环境变量
                content = expand_env_vars(content)
                config[config_file.replace(".yaml", "")] = yaml.safe_load(content)
    
    return config


def expand_env_vars(text: str) -> str:
    """展开文本中的环境变量"""
    import re
    
    def replace_var(match):
        var_name = match.group(1)
        return os.getenv(var_name, match.group(0))
    
    return re.sub(r'\$\{(\w+)\}', replace_var, text)


def ensure_dir(path: Path):
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)
