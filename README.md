# drk_sdk

独立DRK10020受控电源SDK，Python 3.11+，115200/8N1。复制`drk`目录到主程序旁直接`import drk`，无需安装本SDK。串口依赖只需准备一次：`python -m pip install -r requirements.txt`（pyserial）。不依赖CANable、axis、测功机或MCP。

## 六个操作接口

| 接口 | 功能 |
| --- | --- |
| `detect()` | 只枚举CH340候选COM口，不打开、不发查询；CH340不等于DRK型号 |
| `open(port=None, *, timeout=1.0)` | 只有一个候选时自动选口；多候选必须指定COM。只查询并核验DRK10020，返回身份与初始设定 |
| `exchange(voltage, current)` | 一次下发电压V/限流A并接收测量；保留当前SDK缓存的输出/保护位 |
| `enable()` | 开启输出，保留设定并读取设定值核验 |
| `disable()` | 关闭输出，保留设定并读取设定值核验；写入不确定后仍可用 |
| `close()` | 仅关闭串口，可重复调用，输出保持原状态 |

删除原有split send/read和待回复状态。所有连接操作由open所在的同一线程执行，SDK不创建后台线程、不承担实验调度。运行期间电源面板/厂家上位机不要并行修改设定；循环保留的是SDK缓存，不会每次查询完整面板设定。

```python
import drk

info = drk.open()  # 自动选口并核验；不会自动关闭或开启现有输出
try:
    print(info)
    # 参数由应用选择。exchange是控制写入，不是独立只读测量。
    data = drk.exchange(12.0, 0.2)
    print(data['voltage'], data['current'], data['power'])
finally:
    drk.close()
```

需要切换输出时显式调用`drk.enable()`/`drk.disable()`；应用自行决定实验停止顺序，close不隐式断电。

## 数据与时序

常用测量字典字段：voltage V、current A、power W、temperature ℃、fault、enabled；另保留22点的voltage_samples/current_samples/remote_voltage_samples及辅助测量。三个22点数组不带传感器原生采样时标，不能当成22个等间隔独立时间样本。

`sent_at_ns`是主机开始串口write前的`perf_counter_ns()`；`received_at_ns`是收到完整帧后的同一单调时钟。两者不是电源物理采样/输出生效时刻。原Windows Python3.11 GetTickCount64时钟分辨率15.625ms，现用高分辨率QPC。

常规控制帧最小发送间隔50ms，约20Hz；AT/设定事务保留100ms间隔。系统调度、串口和电源回复可能使实际频率下降。无忙轮询、无补发过期时隙、不保证严格50ms。timeout限制各次串口写入/回复等待，整个enable/disable包含多次事务及间隔，不是即时或单个timeout内完成。

exchange成功表示收到了校验和/帧格式合法的测量反馈，不代表已独立核对全部设定。enable/disable会核对完整设定；OFF时按真实固件同时清除输出与补偿位，重新ON恢复此前补偿偏好。

## 异常与恢复

- `ProtocolError`：型号、模式、帧等不符；`WriteUncertainError`：控制写入或其反馈未确认，实际输出可能已改变。
- 不自动重试控制写入。写入不确定后拒绝exchange/enable；仍可disable并核验，或close/open重新读取实际状态。
- KeyboardInterrupt在控制事务中也会留下不确定标记并原样抛出。应用须在finally中明确选择失能或保留输出，再关闭资源。
- 设备支持范围沿用原协议：0–100V，限流0.001–20A。SDK不另加功率/负载控制策略。

## 示例与验证

`python demo.py`自动选口，只查询身份和设定后退出。`python demo.py --port COM9 --poll 5`按原设定收发5秒，保持初始输出状态；Ctrl+C关闭串口，不切断输出。

原提供目录保持不变。离线测试及实测数据位于同级`drk_sdk_review_20261021`，不混入SDK。实测仅证明串口协议与状态/设定读回，不作为输出电压、电流精度或磁粉负载动态验证。
