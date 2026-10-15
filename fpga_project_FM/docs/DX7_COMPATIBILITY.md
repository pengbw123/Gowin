# DX7兼容状态与后续清单

本文专门记录尚未完成的参数，避免后续功能迭代时遗忘。当前目标是听感兼容与实时可演奏，并不宣称逐位复刻YM21280/原机DAC。

## 本版已经实现

- 32种经典算法、6算子Ratio/Fixed模式、反馈和Oscillator Key Sync；
- ROM1A整库32音色片上预设，上电装载0号，CC27数值0～31直接选音色；
- 算子四段Rate/Level EG、非线性Level、Rate Scaling、Keyboard Scaling、Velocity Sensitivity；
- Pitch EG完整约-48～+47.625半音范围，并使用独立的Yamaha Pitch-EG速率映射；
- 六种LFO波形、PMS、AMS、LFO Key Sync，以及“等待后渐入”的两段Delay；
- CC64延音踏板：踩下时Note Off只记录松键，抬起时统一进入第四段释放；
- 14位Pitch Bend、MOD颤音、Chorus和Reverb。

## 后续仍需补齐

1. **音高相关Detune**：本版已改为非线性约±7 cent表，但原机Detune还随键位略变；目前仍是每个算子的常数比例。
2. **Pitch EG逐周期状态机**：范围和速率表已兼容，轨迹仍采用稳定的一阶逼近；后续可改为Dexed/msfa相同的线性log-pitch步进和阈值。
3. **LFO Delay精确时标**：已有等待+渐入两阶段，曲线和时长为硬件友好近似；后续可直接移植msfa的两个32位增量公式。
4. **AMS精确量化**：已从错误的线性增益相减改到对数Level域，但四档深度仍是近似表。
5. **反馈与算子内部位宽**：当前反馈采用前两次样值平均，但量化、移位和溢出规则不是原芯片逐位模型。
6. **Fixed算子的Pitch Bend策略**：Pitch EG/LFO按原意不作用于Fixed算子；外部Pitch Bend是否作用仍需做成用户选项。
7. **Portamento**：接口保留，尚未恢复逐声部滑音状态。
8. **原机DAC/模拟输出特性**：不模拟DX7量化噪声、DAC非线性和模拟滤波；如需复古听感应作为可关闭的后处理效果加入。

## 资源优先级

25K优先保证USB Host、16复音、6算子和稳定50 MHz。精确算法若显著增加DSP/CLS，应先在Mega60K版完成；25K可以通过构建开关在“兼容精度”和“显示/效果器”之间选择。
