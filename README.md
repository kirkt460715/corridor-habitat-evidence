# 生态廊道观测资料

本项目整理道路绿化廊道附近的栖息地斑块、设备观测、人工巡护和生态工程记录，供生态廊道维护人员核对区域之间的观测关系。

## 资料

`data/manifest.json` 记录各本地合成数据源的字段。设备有效时段与动物触发分开保存，工程记录标出影响区段和实施时间。

## 本地检查

使用 Python 3.11 或更新版本，无需安装第三方依赖。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q tests
```
