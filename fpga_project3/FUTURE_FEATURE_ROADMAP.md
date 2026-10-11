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

## 已确认的后续开发顺序（2026-10-11）

1. 先完成当前Primer 25K吉他扫弦版本的实板验证和稳定性调试；在此之前不继续叠加大型算法。
2. 将稳定的USB-MIDI、复音分配、I2S和基础音色链路移植到Tang Mega 60K。
3. 增加Pitch Envelope，支持鼓、贝斯、铜管和拨弦起音的自动音高变化。
4. 增加每复音动态多模式滤波器及Filter Envelope，支持LP/HP/BP/Notch、截止频率、共振、力度映射和键位跟踪。
5. 在总混音后增加三段参数EQ、压缩/限幅和软削波；保留Chorus、Delay、Reverb的独立旁路和A/B试听。
6. 为吉他模式增加六弦Karplus-Strong物理建模；Pad 1/Pad 2继续作为下扫/上扫触发，力度控制音量、亮度和部分扫弦速度。
7. 实现32复音带限波表播放。四基准、32谐波参数仍作为音色描述，可生成分音区波表，避免32复音×32谐波全部实时累加带来的运算压力。
8. 时间允许时增加2-Operator FM起音层，用于电钢琴、钟声、金属打击和鼓点瞬态；不优先实现完整4/6算子FM。
9. 接通Mega 60K的HDMI/DVI显示，优先采用“HDMI只显示、MIDI旋钮/Pad控制”的无电脑方案；软核管理菜单和参数，PL完成音频、FFT和视频扫描。
10. 增加Flash/SD音色预设保存与读取，实现上电独立运行。
11. 最后根据剩余时间决定是否由HDMI升级为RGB电容触摸屏；串口屏只作为低风险备选。

## 当前音色参数状态

- 当前上电谐波默认值来自`tools/instrument_4anchor_relative_decay.json`生成的C2/C3/C4/C6四基准、每基准16谐波数据。
- 当前幅度ADSR默认时间为Attack 30 ms、Decay 500 ms、Sustain 0.18、Release 800 ms。
- 当前A/D/R默认曲线沿用“钢琴（合成）7”的曲率：Attack 0.9、Decay 0.6、Release 0.3，每段256点Q0.16；三张表已经由只读ROM升级为UART可写RAM。
- 已加入256点Pitch Envelope RAM和独立遍历时间：Note On从表头开始，走完后保持末值，ADSR提前结束时随声部结束；默认表为0半音。
- 音色编辑器已经可以编辑、试听、保存、UART下载两组包络，也可导出为下次综合使用的上电初值。
- 当前25K采用单活动RAM，实时下载前应松开琴键；迁移Mega60K后再加入双缓冲曲线表、原子切换以及动态滤波包络。
