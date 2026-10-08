# 后续功能路线（已确认）

> `fpga_project3` 已完成Pitch Bend、Vibrato、Portamento以及Chorus、Delay、Reverb的第一版RTL；以下内容保留为实板试听后的优化路线。

## 表情控制

- Pitch Bend：MIDI Pitch滑条，默认范围±2半音，14位E0消息，全通道活跃声部生效。
- Vibrato：Modulation滑条控制深度，RATE旋钮控制3～9 Hz速度，默认最大约±50 cents。
- Portamento：独立的自动音间滑行功能，只在Mono Lead/Legato模式启用，时间0～500 ms可调。
- 力度：琴键Velocity继续控制音量和音色亮度。

## 吉他拨弦

- 原型：MIDI Pad1/Pad2分别触发向下/向上扫弦，Pad力度控制拨弦强度。
- 成品：增加自研六条电容触摸琴弦，触发顺序判断方向，时间间隔判断扫弦速度。
- 音源：先做加法合成吉他预设，最终优先采用Karplus-Strong物理建模。

## 数字效果器

- 顺序：合成混音 -> Chorus -> Delay -> Reverb -> 平滑限幅器 -> I2S。
- Chorus：5～25 ms短延迟受LFO调制，产生多人/多乐器同时演奏的宽厚感。
- Delay：可调循环延迟和反馈，产生清晰重复回声。
- Reverb：Schroeder多Comb+All-pass结构，产生房间/大厅空间尾音。
- 必须保留即时Dry通路，效果器按逐样本流水实现，不能因块缓存破坏≤5 ms响应。

## 冲奖版本

- Primer 25K先验证Pitch Bend、Vibrato、Portamento、Pad扫弦和三种效果器。
- Mega 60K成品升级为单音32个独立加法分量，分量保留独立相位状态。
- 钢琴使用32分量加法合成；吉他使用Karplus-Strong物理建模；支持独立旁路与现场频谱测试模式。
