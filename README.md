# 生态廊道观测资料

本项目整理道路绿化廊道附近的栖息地斑块、设备观测、人工巡护和生态工程记录，供生态廊道维护人员核对区域之间的观测关系，并支持道路断点动物通行改造的证据复核、方案比较与现场交割。

## 资料

`data/manifest.json` 记录各本地合成数据源的字段。设备有效时段与动物触发分开保存，工程记录标出影响区段和实施时间。

字段含义及有效期约定见[资料口径](资料口径.md)。

## 能力（`corridor/`，仅用 Python 标准库）

| 模块 | 解决的问题 |
| --- | --- |
| `corridor.network` | 斑块—通道图；按日期有效期和施工封闭集枚举实际可走路径 |
| `corridor.events` | 触发去重、过境证据分级、阴性观察、覆盖缺口、斑块对可证范围与互斥路线解释 |
| `corridor.periods` | 同季节、同口径、努力相近时期的对比；不可比时显式给出原因 |
| `corridor.planning` | 改造分期审查：连通承诺、共用通道容量、资源上限、先补查判定与有限设备布置 |
| `corridor.handover` | 单断点交割包：改造前后斑块对、保留路径、切换夜覆盖、未过原因与接续观测 |

关键口径（均有 `tests/` 中的规则测试锁定）：

1. 相同非空事件组必然合并；空组只按实际可走路径、时间窗和方向判断是否*可能*是一次过境，合并与拆分保留为互斥情景，结果给可证下限与相容上界，不给虚假精确值。
2. “进入”不等于完成穿越；只有下游再出现、本通道“离开/经过”才升级为完成穿越证据。
3. 无记录只在有检测机会的覆盖内才是阴性观察；停机/无覆盖窗口只产生覆盖缺口。
4. 入口型覆盖对着多条支路时，一次穿越不能同时算到每条支路；互斥路线解释不得相加成城市总量。
5. 同一 `shared_passage` 的多个保护对象共享夜间容量，需求合并计算，不能各算一份余量。
6. 方案只在未辨清的路线解释下才保得住承诺时，结论是“先补查”，并给出可先行的准备工作和有限设备日下的布置建议。
7. 施工切换恰逢设备断电时，同一条放宽假设不能同时证明两条路线改善，只能列入接续观测。

## 命令行

```sh
# 廊道证据复核（事件分簇、路线解释、可证范围、阴性观察、覆盖缺口）
python3 -m corridor.cli --data data evidence
python3 -m corridor.cli --json --data data evidence

# 平行时期对比（可加 --periods 限定）
python3 -m corridor.cli --data data periods --periods Q-01 Q-02

# 改造方案分期审查与有限设备布置
python3 -m corridor.cli --data data proposals

# 单个断点的交割包
python3 -m corridor.cli --data data handover --proposal A

# 项目人员按一次工程安排查询可执行信息
python3 -m corridor.cli --data data work --proposal C
```

## 本地检查

使用 Python 3.11 或更新版本，无需安装第三方依赖。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q corridor tests
```
