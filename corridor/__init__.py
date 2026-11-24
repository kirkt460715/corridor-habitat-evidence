"""生态廊道证据复核、改造比较与现场交割工具包。

模块划分：

- ``model``    领域数据类与枚举（合成资料的类型化表示）
- ``loading``  读取 ``data/`` 下各 CSV，做时间与有效期解析
- ``network``  斑块—通道图，支持按日期、封闭集枚举可走路径
- ``events``   相机/人工事件去重、过境证据分级、阴性观察与相容路线
- ``periods``  设备布设、季节与努力相近时期的对比
- ``planning`` 改造方案分期审查：连通、共用容量、资源、补查与设备布置
- ``handover`` 单断点改造后的交割包：放行步骤、保留路径、未过原因、接续观测
"""

from .loading import load_dataset
from .model import Dataset

__all__ = ["load_dataset", "Dataset"]
__version__ = "0.1.0"
