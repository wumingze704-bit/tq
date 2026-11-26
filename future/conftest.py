"""让 pytest 能直接 import cnfut（future/ 为策略工程根目录）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
