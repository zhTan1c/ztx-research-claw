# 引用论文


## 第1章　引言　（目标篇幅 800～1000 字，4节，引用约 10 篇）

### 1.1 研究背景与意义
- **论文**: [2] Folding Clothes Autonomously: A Complete Pipeline
  - **详细**: 2016 IEEE Trans. Robotics
  - **简介**: 完整服装折叠流水线，涵盖分拣、识别、展开、铺平、折叠全过程，体现工业自动化对柔性操控的典型需求场景。
  - **链接**: https://ieeexplore.ieee.org/document/7589002/
- **论文**: [4] Deep Transfer Learning of Pick Points on Fabric for Robot Bed-Making
  - **详细**: 2018 ISRR 2019
  - **简介**: 机器人整床任务，说明家庭服务场景对布料抓取点定位的实际需求，以及形变不确定性带来的感知挑战。
  - **链接**: https://arxiv.org/abs/1809.09810
- **论文**: 【待补充】医疗/食品场景柔性操控综述或应用论文
  - **简介**: 补充医疗护理、食品包装等场景的工程需求文献，增强1.1的场景覆盖度。
### 1.2 核心问题界定
- **论文**: [3] Learning Particle Dynamics for Manipulating Rigid Bodies, Deformable Objects, and Fluids
  - **详细**: 2018 arXiv / ICLR 2019
  - **简介**: 粒子动力学学习框架（DPI-Nets），统一建模刚体、柔性体与流体，适合界定柔性物质与刚体在状态空间维度上的本质差异。
  - **链接**: https://arxiv.org/abs/1810.01566
- **论文**: 【待补充】柔性物质力学特性综述/建模论文
  - **简介**: 补充说明布料/绳索/颗粒物的力学行为文献，支撑"大形变、接触拓扑变化"的学术界定。
### 1.3 相关综述对比与本文定位
- **论文**: 【待补充】机器人抓取综述（如 Sahbani et al. 或 Kleeberger et al.）
  - **简介**: 已有机器人抓取综述，用于对比本文在柔性物质和算法范式上的差异化定位。
- **论文**: 【待补充】柔性物体操控综述（如 Sanchez et al. 2018）
  - **简介**: 柔性操控专项综述，与本文定位形成对比：对方侧重感知/建模，本文侧重算法范式演进。
- **论文**: 【待补充】深度强化学习机器人综述（如 Nguyen et al. 或 Ibarz et al.）
  - **简介**: DRL机器人控制综述，与本文对比：覆盖面更广但不聚焦柔性物质，缺少非RL方法链条。
### 1.4 综述结构与主要贡献

## 第2章　面向柔性物质灵巧抓取的非强化学习类算法　（目标篇幅 1000～1500 字，3节，引用约 10～15 篇）

### 2.1 基于模型与规划的方法
- **论文**: [1] Folding Deformable Objects using Predictive Simulation and Trajectory Optimization
  - **详细**: 2015 IROS 2015
  - **简介**: 利用薄壳仿真器离线优化双臂折叠轨迹，以二次目标函数衡量形变误差，典型说明解析模型+轨迹优化的建模思路及其对已知材质参数的依赖。
  - **链接**: https://arxiv.org/abs/1512.06922
- **论文**: [2] Folding Clothes Autonomously: A Complete Pipeline
  - **详细**: 2016 IEEE Trans. Robotics
  - **简介**: 传统感知+规划完整流水线，体现传统方法工程复杂度高、模块间假设强耦合的局限性。
  - **链接**: https://ieeexplore.ieee.org/document/7589002/
- **论文**: 【待补充】有限元仿真用于布料形变建模的代表工作（如 Breen et al. 或 Provot et al.）
  - **简介**: FEM/质点-弹簧建模的奠基论文，说明解析模型精度上限及计算代价，支撑2.1核心论点。
- **论文**: 【待补充】基于位置的动力学（PBD）仿真论文（如 Müller et al. 2007）
  - **简介**: PBD方法在速度与精度之间的取舍，说明实时仿真对模型精度的妥协，为3.1节引出。
### 2.2 感知与示教驱动的方法
- **论文**: [4] Deep Transfer Learning of Pick Points on Fabric for Robot Bed-Making
  - **详细**: 2018 ISRR 2019
  - **简介**: 监督深度迁移学习定位织物抓取点，减少对显式力学模型依赖，但依赖大量标注数据和固定任务设置，泛化性受限。
  - **链接**: https://arxiv.org/abs/1809.09810
- **论文**: [5] Deep Imitation Learning of Sequential Fabric Smoothing From an Algorithmic Supervisor
  - **详细**: 2019 IROS 2020
  - **简介**: 算法监督者生成示教数据+模仿学习，减少人工演示成本，但策略泛化能力受限于监督者覆盖的状态分布。
  - **链接**: https://arxiv.org/abs/1910.04854
- **论文**: 【待补充】视觉伺服用于柔性操控代表论文
  - **简介**: 视觉伺服闭环控制布料形状，说明感知驱动方法减少建模依赖的思路及实时性局限。
- **论文**: 【待补充】运动基元/DMP用于柔性任务代表论文
  - **简介**: 动态运动基元用于编码示教轨迹，说明运动基元方法在变形不确定性下泛化能力不足的局限。
### 2.3 传统方法局限性小结

## 第3章　面向柔性物质灵巧抓取的深度强化学习类算法　（目标篇幅 2500～3000 字，3节，引用约 35～45 篇）★核心章节

### 3.1 仿真环境与任务建模的早期探索
- **论文**: [6] SoftGym: Benchmarking Deep Reinforcement Learning for Deformable Object Manipulation
  - **详细**: 2020 CoRL 2020
  - **简介**: 柔性物体操控专用基准平台，含绳索/布料/流体10种环境，标准化OpenAI Gym接口，是早期DRL探索的基础设施；揭示高维可观测状态对RL算法的挑战。
  - **链接**: https://arxiv.org/abs/2011.07215
- **论文**: [7] Dynamic Cloth Manipulation with Deep Reinforcement Learning
  - **详细**: 2019 arXiv
  - **简介**: 早期仿真环境下布料动态操控DRL探索，说明仿真任务设置简化的原因及动作空间设计选择对后续工作的影响。
  - **链接**: https://arxiv.org/abs/1910.14475
- **论文**: [8] Sim-to-Real Reinforcement Learning for Deformable Object Manipulation
  - **详细**: 2018 Robotics & Autonomous Systems
  - **简介**: 早期仿真到真实迁移DRL探索，分析仿真状态空间设计对迁移效果的影响，是3.3.3节的历史起点。
  - **链接**: https://arxiv.org/abs/1806.07851
- **论文**: [9] Learning to Manipulate Deformable Objects without Demonstrations
  - **详细**: 2019 arXiv
  - **简介**: 无示范学习可变形物体操控，从状态空间简化和自监督角度探索早期任务建模，说明动作空间设计的折中。
  - **链接**: https://arxiv.org/abs/1910.13439
- **论文**: 【待补充】早期布料仿真平台论文（如 ClothSim 或 PyBullet Deformable）
  - **简介**: 补充早期仿真平台能力边界说明，支撑PBD/FEM速度-精度取舍的论点。
### 3.2 基于Actor-Critic的连续控制方法演进
- **论文**: [10] Continuous control with deep reinforcement learning (DDPG)
  - **详细**: 2015 arXiv / ICLR 2016
  - **简介**: 深度确定性策略梯度（DDPG），连续动作空间Actor-Critic基础算法，是柔性抓取进入连续控制范式的起点，说明为何该框架是必要选择。
  - **链接**: https://arxiv.org/abs/1509.02971
- **论文**: [11] Addressing Function Approximation Error in Actor-Critic Methods (TD3)
  - **详细**: 2018 ICML 2018
  - **简介**: TD3通过双Q网络和延迟策略更新缓解函数近似误差导致的训练不稳定，从"估值过高"角度解决柔性高维状态下的不稳定问题。
  - **链接**: https://arxiv.org/abs/1802.09477
- **论文**: [13] Soft Actor-Critic: Off-Policy Maximum Entropy Deep Reinforcement Learning (SAC)
  - **详细**: 2018 ICML 2018
  - **简介**: SAC通过最大熵框架和随机策略解决探索不足与训练不稳定，在柔性高维接触场景下的收敛稳定性优于确定性策略方法。
  - **链接**: https://arxiv.org/abs/1801.01290
- **论文**: [15] Learning Dexterous In-Hand Manipulation (OpenAI Dexterous Hand)
  - **详细**: 2018 IJRR 2020
  - **简介**: 大规模仿真训练（域随机化）灵巧手操控魔方，证明Actor-Critic框架在复杂接触操控任务上的能力上限，是柔性类工作的重要参照基线。
  - **链接**: https://arxiv.org/abs/1808.00177
- **论文**: [12] ★【待补充】TD3在触觉/柔性操控任务中的应用论文
  - **简介**: TD3在具体柔性任务上的验证工作，需提供成功率数据和对比baseline，用于表3-1数据填充。
- **论文**: [14] ★【待补充】SAC在布料折叠任务中的应用论文
  - **简介**: SAC在具体布料折叠任务上的验证工作，需提供成功率数据和对比baseline，用于表3-1数据填充。
- **论文**: 【待补充】PPO在柔性物体操控中的代表论文（Schulman et al. 2017基础 + 应用）
  - **简介**: PPO作为近端策略优化代表，与SAC/TD3形成对比，说明on-policy vs off-policy在柔性任务样本效率上的差异。
### 3.3.1 奖励函数设计
- **论文**: [17] Soft Contact Simulation and Manipulation Learning with Vision-Based Tactile Sensor
  - **详细**: 2025 IEEE T-ASE 2025
  - **简介**: 结合GelSight类触觉传感器进行软接触仿真建模，触觉信号编码进奖励函数，典型说明接触感知奖励的设计方式。
  - **链接**: https://ieeexplore.ieee.org/document/10843970
- **论文**: [20] Sim-to-Real Gentle Manipulation with Stress-Guided Reinforcement Learning
  - **详细**: 2025 arXiv
  - **简介**: 物体内部应力信号作为奖励约束，形变感知奖励的典型设计，说明如何将柔性物质物理特性编码进奖励信号。
  - **链接**: https://arxiv.org/abs/2510.25405
- **论文**: [21] DeformPAM: Data-Efficient Learning for Long-Horizon Deformable Object Manipulation
  - **详细**: 2024 ICRA 2025
  - **简介**: 基于偏好对齐的动作学习，间接解决柔性任务奖励稀疏和目标状态定义模糊的问题，是奖励信号设计的新思路。
  - **链接**: https://arxiv.org/abs/2309.12300
- **论文**: 【待补充】稠密奖励 vs 稀疏奖励对比实验论文（柔性任务场景）
  - **简介**: 提供柔性抓取场景下稀疏与稠密奖励对比的量化数据，支撑"二值稀疏奖励不适用"的论断。
- **论文**: 【待补充】接触丰富操控奖励设计论文（如 contact-rich manipulation reward shaping）
  - **简介**: 面向接触丰富场景的奖励塑形方法，补充说明接触感知奖励的通用设计原则。
### 3.3.2 样本效率提升
- **论文**: [9] Learning to Manipulate Deformable Objects without Demonstrations
  - **详细**: 2019 arXiv
  - **简介**: 无示范学习框架，通过视觉目标条件和自监督提升样本效率，讨论HER类目标重标注在布料任务上的适用性边界。
  - **链接**: https://arxiv.org/abs/1910.13439
- **论文**: [21] DeformPAM: Data-Efficient Learning for Long-Horizon Deformable Object Manipulation
  - **详细**: 2024 ICRA 2025
  - **简介**: 少量偏好数据高效学习长时序操控，直接对应柔性任务样本数据稀缺的核心问题，说明偏好对齐比传统演示数据更高效的原因。
  - **链接**: https://arxiv.org/abs/2309.12300
- **论文**: [25] Disentangling perception and reasoning for improving data efficiency in cloth manipulation
  - **详细**: 2026 arXiv
  - **简介**: 解耦感知与推理模块提升数据效率，是样本效率提升的最新进展，说明模块化架构对减少数据需求的贡献。
  - **链接**: https://arxiv.org/abs/2601.21713
- **论文**: 【待补充】HER（Hindsight Experience Replay, Andrychowicz et al. 2017）
  - **简介**: HER目标重标注提升稀疏奖励下样本效率，是3.3.2必须讨论的基础方法，需分析其在柔性任务目标状态定义上的局限。
- **论文**: 【待补充】课程学习用于柔性操控代表论文
  - **简介**: 从简单形变到复杂形变的任务课程设计，提供柔性任务课程学习的具体实现和量化收益数据。
### 3.3.3 Sim-to-Real 迁移
- **论文**: [8] Sim-to-Real Reinforcement Learning for Deformable Object Manipulation
  - **详细**: 2018 Robotics & Autonomous Systems
  - **简介**: 可变形物体操控Sim-to-Real迁移早期工作，分析仿真与真实之间的域差距来源及缓解方法，是3.3.3历史起点。
  - **链接**: https://arxiv.org/abs/1806.07851
- **论文**: [16] Domain Randomization for Transferring Deep Neural Networks from Simulation to the Real World
  - **详细**: 2017 IROS 2017
  - **简介**: 域随机化方法提出，随机化视觉渲染参数使策略对外观变化鲁棒，是Sim-to-Real的基础方法；需说明其在柔性材质参数不确定性上的局限。
  - **链接**: https://arxiv.org/abs/1703.06907
- **论文**: [20] Sim-to-Real Gentle Manipulation with Stress-Guided Reinforcement Learning
  - **详细**: 2025 arXiv
  - **简介**: 应力引导RL+Sim-to-Real，处理柔性材质参数不确定性，说明物理感知约束对迁移效果的改善程度，提供真实场景验证数据。
  - **链接**: https://arxiv.org/abs/2510.25405
- **论文**: [24] Residual Reinforcement Learning for Robot Control
  - **详细**: 2018 ICRA 2019
  - **简介**: 残差RL在传统控制器基础上叠加学习残差策略，弥补仿真到真实的动力学差距，是自适应迁移策略的代表方法。
  - **链接**: https://arxiv.org/abs/1812.03201
- **论文**: [15] Learning Dexterous In-Hand Manipulation
  - **详细**: 2018 IJRR 2020
  - **简介**: 灵巧手操控中大规模域随机化的成功案例，提供刚体场景迁移的对比基准，用于对比说明柔性材质参数随机化的额外困难。
  - **链接**: https://arxiv.org/abs/1808.00177
- **论文**: 【待补充】自适应域随机化或系统辨识用于柔性迁移的论文
  - **简介**: 自适应域随机化或在线系统辨识方法，解决柔性材质参数分布难以先验确定的问题，说明现有方法的进展边界。

## 第4章　面向柔性物质灵巧抓取的混合与前沿算法　（目标篇幅 3000～3500 字，5节，引用约 35～45 篇）★核心章节

### 4.1 感知–控制融合
- **论文**: [17] Soft Contact Simulation and Manipulation Learning with Vision-Based Tactile Sensor
  - **详细**: 2025 IEEE T-ASE 2025
  - **简介**: 视觉触觉传感器+软接触仿真+操控学习一体化，端到端感知-控制融合，触觉局部形变信息通过梯度回传被有效利用。
  - **链接**: https://ieeexplore.ieee.org/document/10843970
- **论文**: [18] GelSight: High-Resolution Robot Tactile Sensors for Estimating Geometry and Force
  - **详细**: 2017 Sensors 2017
  - **简介**: 高分辨率视觉触觉传感器，提供局部接触面几何和力分布信息，是4.1端到端视触觉融合的感知硬件基础。
  - **链接**: https://www.mdpi.com/1424-8220/17/12/2762
- **论文**: [19] ViTacFormer: Learning Cross-Modal Representation for Visuo-Tactile Dexterous Manipulation
  - **详细**: 2025 arXiv
  - **简介**: 视触觉跨模态Transformer，端到端学习统一表征，梯度从灵巧操控目标同时传回视觉和触觉编码器，是4.1的前沿代表。
  - **链接**: https://arxiv.org/abs/2506.15953
- **论文**: [35] Bi-Touch: Bimanual Tactile Manipulation with Sim-to-Real Deep Reinforcement Learning
  - **详细**: 2023 IEEE RA-L 2023
  - **简介**: 双臂触觉操控端到端框架，Sim-to-Real深度RL，触觉信息在双臂协同中的感知-控制一体化，提供量化迁移效果数据。
  - **链接**: https://arxiv.org/abs/2307.16208
- **论文**: 【待补充】端到端视觉运动策略用于柔性抓取代表论文（如 Levine et al. end-to-end visuomotor）
  - **简介**: 端到端视觉运动策略的基础工作，说明感知-控制梯度联合优化在柔性任务上的具体效果。
### 4.2 模型–学习融合（MBRL）
- **论文**: [22] Dream to Control: Learning Behaviors by Latent Imagination (Dreamer)
  - **详细**: 2019 ICLR 2020
  - **简介**: Dreamer通过潜在世界模型在潜在空间做虚拟rollout优化策略，是MBRL在高维视觉输入下的代表方法，讨论其用于柔性场景的适用性。
  - **链接**: https://arxiv.org/abs/1912.01603
- **论文**: [23] When to Trust Your Model: Model-Based Policy Optimization (MBPO)
  - **详细**: 2019 NeurIPS 2019
  - **简介**: MBPO分析模型误差积累规律，通过短步展开的虚拟rollout辅助无模型优化，讨论接触拓扑变化对模型误差的影响。
  - **链接**: https://arxiv.org/abs/1906.08253
- **论文**: [32] Learning Latent Dynamics for Planning from Pixels (PlaNet)
  - **详细**: 2018 ICML 2019
  - **简介**: 从像素学习循环状态空间模型（RSSM）并在潜在空间规划，是Dreamer的前身，说明潜在动力学学习的基础方法。
  - **链接**: https://arxiv.org/abs/1811.04551
- **论文**: [34] Real-Time Neural MPC: Deep Learning MPC for Quadrotors and Agile Robotic Platforms
  - **详细**: 2022 IEEE RA-L 2023
  - **简介**: 神经网络动力学模型嵌入MPC求解器，实现实时控制，说明模型-学习融合在工程部署上的可行性，为柔性任务MBRL提供参考路径。
  - **链接**: https://arxiv.org/abs/2203.07747
- **论文**: 【待补充】基于学习的柔性物体动力学模型论文（如 Haar et al. 或 DPI-Nets应用）
  - **简介**: 专门针对柔性体接触动力学的学习模型，说明接触拓扑变化对MBRL模型精度的影响，是4.2核心论点的直接支撑。
### 4.3 多模态融合
- **论文**: [17] Soft Contact Simulation and Manipulation Learning with Vision-Based Tactile Sensor
  - **详细**: 2025 IEEE T-ASE 2025
  - **简介**: 在4.3中侧重视觉与触觉信息的互补融合机制，而非端到端架构（区别于4.1），分析接触前后模态置信度切换的处理方式。
  - **链接**: https://ieeexplore.ieee.org/document/10843970
- **论文**: [18] GelSight: High-Resolution Robot Tactile Sensors
  - **详细**: 2017 Sensors 2017
  - **简介**: 在4.3中侧重触觉模态的信号特性（采样频率、空间分辨率），说明触觉信号在多模态融合中与视觉/力觉的时序对齐挑战。
  - **链接**: https://www.mdpi.com/1424-8220/17/12/2762
- **论文**: [19] ViTacFormer: Learning Cross-Modal Representation for Visuo-Tactile Dexterous Manipulation
  - **详细**: 2025 arXiv
  - **简介**: 在4.3中侧重跨模态时序对齐机制，Transformer的注意力机制如何处理视觉和触觉不同采样频率的对齐问题。
  - **链接**: https://arxiv.org/abs/2506.15953
- **论文**: [35] Bi-Touch: Bimanual Tactile Manipulation
  - **详细**: 2023 IEEE RA-L 2023
  - **简介**: 在4.3中侧重双臂视觉-触觉协同，说明多模态信号在双臂协同任务中的时序协调和置信度加权机制。
  - **链接**: https://arxiv.org/abs/2307.16208
- **论文**: 【待补充】视觉+力觉融合用于柔性操控的代表论文
  - **简介**: 力觉（F/T传感器）与视觉融合的工作，补全视觉-触觉-力觉三模态的完整讨论，说明全局受力信号的采样频率和融合方式。
### 4.4 大规模并行仿真
- **论文**: [39] Isaac Gym: High Performance GPU-Based Physics Simulation For Robot Learning
  - **详细**: 2021 NeurIPS 2021
  - **简介**: GPU并行仿真基础平台，数千并行环境大幅加速RL训练，使原本样本效率问题导致无法收敛的柔性任务策略变得可行；需说明其柔性仿真精度局限。
  - **链接**: https://arxiv.org/abs/2108.10470
- **论文**: [40] Learning to Walk in Minutes Using Massively Parallel Deep Reinforcement Learning
  - **详细**: 2021 CoRL 2022
  - **简介**: 大规模并行RL数分钟内训练四足行走策略，量化说明GPU并行训练范式转变的效率增益，为柔性任务提供并行训练的参照数据。
  - **链接**: https://arxiv.org/abs/2109.11978
- **论文**: [15] Learning Dexterous In-Hand Manipulation
  - **详细**: 2018 IJRR 2020
  - **简介**: 大规模仿真训练灵巧手的先驱，并行训练范式的早期验证，与IsaacGym时代的训练效率形成历史对比。
  - **链接**: https://arxiv.org/abs/1808.00177
- **论文**: 【待补充】IsaacLab或柔性物体GPU仿真精度分析论文
  - **简介**: 评估Isaac系列平台在柔性物体仿真（布料/软体）精度方面的局限性，支撑"诚实说明仿真器柔性精度局限"的写作要求。
### 4.5 基础模型驱动的抓取策略
- **论文**: [27] Transporter Networks: Rearranging the Visual World for Robotic Manipulation
  - **详细**: 2020 CoRL 2020
  - **简介**: 视觉空间拾取-放置框架，无需显式物体表征，是VLM类方法的视觉操控前身，说明从感知表征直接到操控动作的早期探索。
  - **链接**: https://arxiv.org/abs/2010.14406
- **论文**: [28] CLIPort: What and Where Pathways for Robotic Manipulation
  - **详细**: 2021 CoRL 2021
  - **简介**: CLIP语义理解+Transporter空间精度，语言-视觉融合操控，VLA类方法的早期形态，说明语义指令如何被分解为空间动作。
  - **链接**: https://arxiv.org/abs/2109.12098
- **论文**: [26] Equivariant Transporter Network
  - **详细**: 2022 arXiv / RSS 2022
  - **简介**: 等变性引入Transporter，旋转等变卷积提升几何泛化能力，说明几何先验如何嵌入基础模型类方法以提升柔性任务中的姿态泛化。
  - **链接**: https://arxiv.org/abs/2202.09400
- **论文**: [30] Dense Object Nets: Learning Dense Visual Object Descriptors By and For Robotic Manipulation
  - **详细**: 2018 CoRL 2018
  - **简介**: 像素级稠密视觉描述符，支持可变形物体的精确目标点定位，是VLA类方法感知表征的早期基础工作。
  - **链接**: https://arxiv.org/abs/1806.08756
- **论文**: [36] RT-1: Robotics Transformer for Real-World Control at Scale
  - **详细**: 2022 arXiv / RSS 2023
  - **简介**: 大规模机器人Transformer，700+真实任务训练，VLA类方法代表，说明从语义指令到动作token的输出范式及其与第3章关节角控制的根本区别。
  - **链接**: https://arxiv.org/abs/2212.06817
- **论文**: [37] RT-2: Vision-Language-Action Models Transfer Web Knowledge to Robotic Control
  - **详细**: 2023 CoRL 2023
  - **简介**: 预训练VLM微调为VLA，将互联网语义知识迁移至机器人控制，说明预训练基础对跨任务泛化能力的贡献及动作原语语义粒度的提升。
  - **链接**: https://arxiv.org/abs/2307.15818
- **论文**: [38] Octo: An Open-Source Generalist Robot Policy
  - **详细**: 2024 arXiv / RSS 2024
  - **简介**: 开源通用机器人策略，Open X-Embodiment数据集训练，支持多具身形态微调，是VLA类方法的可复现基准，用于表4-1数据填充。
  - **链接**: https://arxiv.org/abs/2405.12213
- **论文**: [41] Open X-Embodiment: Robotic Learning Datasets and RT-X Models
  - **详细**: 2023 ICRA 2024
  - **简介**: 跨具身形态大规模数据集+RT-X通用模型，基础模型训练的数据基础，说明数据规模对跨任务泛化能力的支撑作用。
  - **链接**: https://arxiv.org/abs/2310.08864
- **论文**: [42] RoboCat: A Self-Improving Generalist Agent for Robotic Manipulation
  - **详细**: 2023 arXiv
  - **简介**: 自我改进通用机器人智能体，收集自身演示数据迭代微调，说明基础模型驱动方法的自进化能力及其对柔性任务的适用边界。
  - **链接**: https://arxiv.org/abs/2306.11706
- **论文**: 【待补充★重要】Diffusion Policy（Chi et al. 2023）
  - **简介**: 扩散策略的核心论文，多模态动作分布建模，适合布料接触高不确定性场景，是4.5节与ACT对比的核心方法之一，必须补充。
- **论文**: 【待补充★重要】ACT: Action Chunking with Transformers（Zhao et al. 2023）
  - **简介**: 动作分块Transformer，适合长时序协调抓取任务，与扩散策略形成任务适配性对比，是4.5节必须讨论的前沿方法。
- **论文**: [25] Disentangling perception and reasoning for cloth manipulation
  - **详细**: 2026 arXiv
  - **简介**: 感知与推理解耦的模块化设计与基础模型的模块化思路相关，可作为4.5节模块化动作表示讨论的补充视角。
  - **链接**: https://arxiv.org/abs/2601.21713

## 第5章　实验评估与性能分析　（目标篇幅 1000～1500 字，5节，引用约 15～20 篇）

### 5.1 仿真平台与数据集概览
- **论文**: [6] SoftGym: Benchmarking Deep Reinforcement Learning for Deformable Object Manipulation
  - **详细**: 2020 CoRL 2020
  - **简介**: 柔性物体专用平台，能力边界：支持布料/绳索/流体，PBD仿真精度有限，不适合高精度接触建模；适用场景：DRL基准测试。
  - **链接**: https://arxiv.org/abs/2011.07215
- **论文**: [39] Isaac Gym: High Performance GPU-Based Physics Simulation
  - **详细**: 2021 NeurIPS 2021
  - **简介**: GPU并行仿真平台，能力边界：柔性体仿真精度低于专用布料仿真器，适用场景：大规模并行RL训练、硬件机器人学习。
  - **链接**: https://arxiv.org/abs/2108.10470
- **论文**: [3] Learning Particle Dynamics (DPI-Nets)
  - **详细**: 2018 arXiv / ICLR 2019
  - **简介**: 粒子仿真框架，能力边界：统一建模刚体/软体/流体，精度依赖粒子数量，适用场景：动力学模型学习和基于模型的规划研究。
  - **链接**: https://arxiv.org/abs/1810.01566
- **论文**: 【待补充】MuJoCo/PyBullet在柔性仿真中的能力分析文献
  - **简介**: 说明MuJoCo和PyBullet对柔性体仿真的支持程度、能力边界和适用场景，与SoftGym/IsaacGym形成平台对比。
### 5.2 评价指标体系与公平对比原则
- **论文**: [6] SoftGym / [8] Matas et al. / [9] Wu et al.
  - **详细**: 2018–2020 多源
  - **简介**: 三篇工作对"成功率"定义不同（SoftGym用覆盖率、Matas用任务完成二值判断、Wu用目标状态距离阈值），用于说明跨论文对比的公平性问题。
- **论文**: [20] Ikemura et al. / [21] DeformPAM
  - **详细**: 2024–2025 多源
  - **简介**: 近期工作的成功率定义（应力约束满足率、长时序任务完成率），说明指标多样性随任务复杂度增加而加剧，需统一标准。
- **论文**: 【待补充】机器人操控评估指标综述或标准化论文
  - **简介**: 提供操控任务评估指标的系统化讨论，支撑本文"统一对比标准"的原则说明。
### 5.3 核心性能横向对比
### 5.4 方法适配性与计算资源分析
- **论文**: [39] Isaac Gym / [40] Rudin et al.
  - **详细**: 2021 NeurIPS 2021 / CoRL 2022
  - **简介**: 提供GPU并行训练的量化资源数据：[40]给出"数分钟内收敛"的训练时长数据，[39]提供GPU显存占用基准数据。
- **论文**: [15] Andrychowicz et al. (Dexterous Hand)
  - **详细**: 2018 IJRR 2020
  - **简介**: 大规模仿真训练的资源消耗历史对比数据，与现代GPU并行训练效率形成对比，说明训练范式演进的资源效率提升。
  - **链接**: https://arxiv.org/abs/1808.00177
### 5.5 场景化选型建议

## 第6章　核心挑战与未来发展趋势　（目标篇幅 1000 字，引用约 10 篇，多引 2023 年后工作）

### 6.1 感知挑战
- **论文**: [18] GelSight: High-Resolution Robot Tactile Sensors
  - **详细**: 2017 Sensors 2017
  - **简介**: 当前触觉传感的能力边界（高分辨率局部接触），说明感知挑战：GelSight类传感器仍难以实现大面积实时接触力场感知，全局形变感知依赖视觉。
  - **链接**: https://www.mdpi.com/1424-8220/17/12/2762
- **论文**: [19] ViTacFormer: Cross-Modal Representation for Visuo-Tactile Manipulation
  - **详细**: 2025 arXiv
  - **简介**: 跨模态感知进展，说明现有工作在视触觉融合上走了多远（Transformer级别的表征学习），以及遮挡和实时处理上还差什么。
  - **链接**: https://arxiv.org/abs/2506.15953
- **论文**: 【待补充】实时布料点云处理/形变重建论文（2023年后）
  - **简介**: 实时点云处理和布料形变重建的最新进展，说明感知挑战中形变建模和遮挡处理的当前能力边界。
### 6.2 控制挑战
- **论文**: [20] Sim-to-Real Gentle Manipulation with Stress-Guided RL
  - **详细**: 2025 arXiv
  - **简介**: 接触丰富操控的现状，应力引导方法在易碎/柔性物体轻柔操控上的进展，以及精细力控制在真实场景下的剩余差距。
  - **链接**: https://arxiv.org/abs/2510.25405
- **论文**: [35] Bi-Touch: Bimanual Tactile Manipulation
  - **详细**: 2023 IEEE RA-L 2023
  - **简介**: 双臂精细触觉控制的进展与边界，说明灵巧指尖控制在多指协同和接触力精度上已走多远，以及柔性任务中仍存在的控制鲁棒性挑战。
  - **链接**: https://arxiv.org/abs/2307.16208
- **论文**: [15] Learning Dexterous In-Hand Manipulation
  - **详细**: 2018 IJRR 2020
  - **简介**: 灵巧指尖控制能力边界的历史参照，说明刚体多指操控已达到的水平，对比柔性物体精细控制仍面临的额外挑战（形变不可预测）。
  - **链接**: https://arxiv.org/abs/1808.00177
### 6.3 学习挑战
- **论文**: [21] DeformPAM: Data-Efficient Learning
  - **详细**: 2024 ICRA 2025
  - **简介**: 数据稀缺问题的缓解程度：偏好对齐方法在少量数据下的效果，说明学习挑战中数据效率已走多远，以及长时序任务中仍存在的瓶颈。
  - **链接**: https://arxiv.org/abs/2309.12300
- **论文**: [25] Disentangling perception and reasoning for cloth manipulation
  - **详细**: 2026 arXiv
  - **简介**: 数据效率最新进展，说明解耦架构在减少数据需求上走了多远，以及泛化性挑战（训练分布外布料形态）仍存在的差距。
  - **链接**: https://arxiv.org/abs/2601.21713
- **论文**: [16] Domain Randomization (Tobin et al. 2017)
  - **详细**: 2017 IROS 2017
  - **简介**: 迁移困难挑战的方法基础，说明域随机化在柔性材质参数不确定性上的局限，以及迁移挑战在现有方法框架下的剩余差距。
  - **链接**: https://arxiv.org/abs/1703.06907
### 6.4 未来趋势
- **论文**: [37] RT-2: Vision-Language-Action Models
  - **详细**: 2023 CoRL 2023
  - **简介**: 通用操作基础模型的发展路径代表，说明VLA模型走向柔性任务泛化的具体技术路径（预训练知识迁移）及其关键障碍（柔性形变的语义理解）。
  - **链接**: https://arxiv.org/abs/2307.15818
- **论文**: [38] Octo: An Open-Source Generalist Robot Policy
  - **详细**: 2024 arXiv / RSS 2024
  - **简介**: 通用策略模型的开放问题：Octo在柔性任务上的泛化边界，说明具身智能融合路径面临的关键障碍（柔性物体数据稀缺、形变状态难标注）。
  - **链接**: https://arxiv.org/abs/2405.12213
- **论文**: [41] Open X-Embodiment
  - **详细**: 2023 ICRA 2024
  - **简介**: 数据基础建设方向：跨具身形态数据集的构建路径，说明柔性物体操控数据稀缺的结构性原因及标注成本这一关键障碍。
  - **链接**: https://arxiv.org/abs/2310.08864
- **论文**: [34] Real-Time Neural MPC
  - **详细**: 2022 IEEE RA-L 2023
  - **简介**: 真实部署的技术路径：神经网络MPC实现实时控制，说明柔性任务在工程部署上走向实用面临的推理延迟和模型精度这两个关键障碍。
  - **链接**: https://arxiv.org/abs/2203.07747

## 第7章　总结与展望　（目标篇幅 800 字，引用约 5 篇）

### 7.1 主要工作回顾
- **论文**: [20] Ikemura et al. 2025 (Stress-Guided RL)
  - **详细**: 2025 arXiv
  - **简介**: 物理感知学习在柔性操控中的代表性进展，作为DRL改进路线的总结性引用，串联第3章核心论点。
  - **链接**: https://arxiv.org/abs/2510.25405
- **论文**: [37] RT-2 / [38] Octo
  - **详细**: 2023–2024 多源
  - **简介**: 基础模型驱动方向的代表性进展，作为第4章混合前沿方法的总结性引用，串联全文从算法演进到泛化能力提升的主线。
- **论文**: [6] SoftGym (Lin et al. 2020)
  - **详细**: 2020 CoRL 2020
  - **简介**: 评估体系的代表，作为第5章实验评估体系的总结性引用，说明标准化评估对领域进展的推动作用。
  - **链接**: https://arxiv.org/abs/2011.07215
### 7.2 开放问题与研究路线图