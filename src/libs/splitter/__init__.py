"""文本切分器接口的兼容导出。"""

from src.libs.splitter.base_splitter import BaseSplitter
from src.libs.splitter.recursive_splitter import RecursiveSplitter
from src.libs.splitter.splitter_factory import SplitterFactory, create_splitter

__all__ = ["BaseSplitter", "RecursiveSplitter", "SplitterFactory", "create_splitter"]
