"use client";

import { type CSSProperties, useState } from "react";
import {
  ArrowRight,
  BrainCircuit,
  Download,
  FileStack,
  Github,
  Layers3,
  Rocket,
  ShieldCheck,
  Sparkles,
  Star,
} from "lucide-react";

const githubUrl = "https://github.com/qzzqzzb/OpenClaudeScience";
const macDownloadUrl = "https://github.com/qzzqzzb/OpenClaudeScience/releases";

const demoLoops = [
  {
    path: "/showcase/pbtio3-analysis.gif",
    title: "PbTiO3 钙钛矿替换分析",
    copy: "围绕 A 位/B 位替换，生成结构文件、预测畸变并整理分析报告。",
    tone: "amber",
  },
  {
    path: "/showcase/caffeine-analysis.gif",
    title: "咖啡因计算化学研究",
    copy: "生成结构、计算轨道与红外光谱，并把图片和报告留在项目里。",
    tone: "blue",
  },
  {
    path: "/showcase/y-mixer-model.gif",
    title: "Y 型微流控混合器建模",
    copy: "从建模请求到速度场、浓度场和对比图，串起一次仿真分析。",
    tone: "cyan",
  },
];

type CapabilityMedia = {
  path: string;
  title: string;
  copy: string;
  kind?: "image" | "video";
};

const capabilityMedia: CapabilityMedia[] = [
  {
    path: "/showcase/science-data-formats.mov",
    title: "丰富科学数据格式",
    copy: "支持分子结构、图像、谱图、表格、报告和计算产物等多种科研数据格式预览。",
    kind: "video",
  },
  {
    path: "/showcase/compute-resources.png",
    title: "计算资源",
    copy: "从本机 SSH 配置选择 Linux 主机，把远程计算资源接入会话审批流。",
  },
  {
    path: "/showcase/approval-mode.png",
    title: "授权模式",
    copy: "按项目风险选择自动授权、写入需审批或全部需审批。",
  },
  {
    path: "/showcase/skills-library.png",
    title: "技能系统",
    copy: "管理内置技能和科学技能库，也可以添加本地技能或从 GitHub 导入。",
  },
  {
    path: "/showcase/connectors.png",
    title: "连接器",
    copy: "集中配置 MCP server 和 SCP Hub Key，让科学技能调用外部工具。",
  },
];

const projectHighlights = [
  {
    icon: Rocket,
    title: "远程计算资源",
    copy: "连接 SSH 计算主机，查看诊断日志，在对话中审阅并确认远程计算任务。",
  },
  {
    icon: Sparkles,
    title: "科学技能开箱即用",
    copy: "文献调研、结果分析、图表、论文写作、文档、幻灯片和领域 workflow 可直接复用。",
  },
  {
    icon: Layers3,
    title: "MCP/SCP 科研工具生态",
    copy: "接入外部工具、数据库和服务；对接中国航天科技体系与创新研究院 SCP 能力生态，让领域工具进入工作台。",
  },
  {
    icon: BrainCircuit,
    title: "国产模型灵活接入",
    copy: "可连接 DeepSeek、通义千问、GLM 等国产模型服务，即将支持中国航天科技体系与创新研究院集思国产模型免费平台。",
  },
  {
    icon: ShieldCheck,
    title: "本地优先，数据可控",
    copy: "项目文件、密钥和运行状态默认留在你控制的机器上。",
  },
  {
    icon: FileStack,
    title: "面向真实科研文件",
    copy: "支持 PDF、Office、图片、分子结构、科学数据输出和生成 artifact 的浏览、搜索、预览和引用。",
  },
];

function MediaSlot({
  path,
  title,
  copy,
  tone,
  featured = false,
}: {
  path: string;
  title: string;
  copy: string;
  tone: string;
  featured?: boolean;
}) {
  const glow =
    tone === "amber"
      ? "rgba(245,184,91,0.5)"
      : tone === "cyan"
        ? "rgba(45,212,191,0.42)"
        : "rgba(91,143,214,0.5)";

  return (
    <article
      className={[
        "ocs-media group relative overflow-hidden rounded-lg border border-white/15 bg-[#0a1830] text-white shadow-2xl shadow-[#0a1830]/30",
        featured ? "lg:col-span-2" : "",
      ].join(" ")}
      style={{ "--slot-glow": glow, aspectRatio: "2560 / 1276" } as CSSProperties}
    >
      <img
        src={path}
        alt=""
        className="absolute inset-0 h-full w-full object-contain"
      />
      <div className="relative flex h-full flex-col justify-between p-5">
        <div className="flex items-center justify-end gap-4">
          <span className="hidden h-2 w-24 rounded-full bg-[linear-gradient(90deg,#f5b85b,#5b8fd6,#2dd4bf)] opacity-80 sm:block" />
        </div>
        <div className="w-fit max-w-2xl rounded-lg border border-white/16 bg-black/50 p-4 backdrop-blur-md">
          <h3 className="max-w-xl text-2xl font-semibold tracking-normal !text-white sm:text-3xl">
            {title}
          </h3>
          <p className="mt-3 max-w-md text-sm leading-6 text-white/72">
            {copy}
          </p>
        </div>
      </div>
    </article>
  );
}

function WorkflowSwitcher() {
  return (
    <div className="grid gap-5">
      {demoLoops.map((story) => (
        <MediaSlot
          key={story.path}
          {...story}
        />
      ))}
    </div>
  );
}

function CapabilityVisual({
  path,
  title,
  kind = "image",
}: {
  path: string;
  title: string;
  kind?: "image" | "video";
}) {
  return (
    <div className="relative aspect-[16/10] overflow-hidden rounded-lg border border-[#d1dae7] bg-white">
      {kind === "video" ? (
        <video
          src={path}
          className="absolute inset-0 h-full w-full object-contain"
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
        />
      ) : (
        <img
          src={path}
          alt={title}
          className="absolute inset-0 h-full w-full object-contain"
        />
      )}
    </div>
  );
}

function CapabilityStack() {
  const [activeIndex, setActiveIndex] = useState(0);
  const active = capabilityMedia[activeIndex];

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
      <article className="overflow-hidden rounded-lg border border-[#d1dae7] bg-white shadow-2xl shadow-[#1e5cb3]/12">
        <CapabilityVisual
          path={active.path}
          title={active.title}
          kind={active.kind}
        />
        <div className="p-6">
          <p className="text-sm font-black uppercase tracking-normal text-[#1e5cb3]">
            {active.kind === "video" ? "科学数据预览" : "功能实景"}
          </p>
          <h3 className="mt-3 text-3xl font-black leading-tight tracking-normal text-[#1e1233]">
            {active.title}
          </h3>
          <p className="mt-4 max-w-3xl text-base leading-7 text-[#706b78]">
            {active.copy}
          </p>
          {active.kind === "video" ? (
            <div className="mt-6 grid gap-3 text-sm font-semibold text-[#1e1233] sm:grid-cols-4">
              {["分子结构", "谱图图片", "表格数据", "分析报告"].map((item) => (
                <span
                  key={item}
                  className="rounded-md border border-[#d1dae7] bg-[#f6f3fb] px-3 py-2"
                >
                  {item}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </article>

      <div className="flex flex-col gap-0 lg:pt-7">
        {capabilityMedia.map((item, index) => {
          const isActive = index === activeIndex;
          return (
            <button
              key={item.path}
              type="button"
              onClick={() => setActiveIndex(index)}
              className={[
                "relative w-full rounded-lg border bg-white p-4 text-left shadow-sm transition duration-200",
                index > 0 ? "-mt-2" : "",
                isActive
                  ? "z-20 border-[#1e5cb3] shadow-xl shadow-[#1e5cb3]/18"
                  : "z-10 border-[#d1dae7] hover:z-30 hover:-translate-x-1 hover:border-[#7ba3e0] hover:shadow-lg",
              ].join(" ")}
              aria-pressed={isActive}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-black uppercase tracking-normal text-[#1e5cb3]">
                    {String(index + 1).padStart(2, "0")}
                  </p>
                  <h4 className="mt-2 text-lg font-black tracking-normal text-[#1e1233]">
                    {item.title}
                  </h4>
                </div>
                <span
                  className={[
                    "mt-1 h-2.5 w-2.5 shrink-0 rounded-full",
                    isActive ? "bg-[#1e5cb3]" : "bg-[#d1dae7]",
                  ].join(" ")}
                />
              </div>
              <p
                className={[
                  "overflow-hidden text-sm leading-6 text-[#706b78] transition-all duration-200",
                  isActive ? "mt-3 max-h-24 opacity-100" : "mt-0 max-h-0 opacity-0",
                ].join(" ")}
              >
                {item.copy}
              </p>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function HeroScene() {
  return (
    <div
      aria-hidden="true"
      className="absolute inset-0 overflow-hidden"
    >
      <div className="absolute inset-0 bg-[#0a1830]" />
      <div
        className="absolute inset-0 opacity-28"
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,0.12) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.12) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
        }}
      />
      <div className="absolute inset-0 bg-[linear-gradient(110deg,#0a1830_0%,#122448_34%,rgba(18,58,139,0.78)_62%,rgba(15,8,28,0.94)_100%)]" />
      <div className="ocs-energy absolute left-[-18%] top-[18%] h-28 w-[82%] rotate-[-10deg] bg-[linear-gradient(90deg,transparent,rgba(245,184,91,0.18),rgba(91,143,214,0.42),transparent)] blur-[1px]" />
      <div className="ocs-energy ocs-energy-delay absolute bottom-[17%] right-[-12%] h-24 w-[74%] rotate-[-10deg] bg-[linear-gradient(90deg,transparent,rgba(45,212,191,0.16),rgba(91,143,214,0.32),transparent)] blur-[1px]" />
      <div
        className="absolute -right-10 top-16 hidden text-[6rem] font-black uppercase leading-none text-transparent opacity-[0.1] md:block lg:text-[8.8rem]"
        style={{ WebkitTextStroke: "1px rgba(255,255,255,0.72)" }}
      >
        科研
        <br />
        工作台
      </div>
      <div className="absolute inset-y-0 right-0 hidden w-1/2 border-l border-white/10 bg-[linear-gradient(90deg,transparent,rgba(255,255,255,0.05))] md:block" />

      <div className="ocs-stage absolute right-[-24rem] top-20 hidden w-[620px] rotate-[-3deg] opacity-35 md:block lg:right-[-5rem] lg:top-24 lg:w-[780px] lg:opacity-100">
        <div className="relative overflow-hidden rounded-lg border border-white/18 bg-white/[0.08] shadow-2xl shadow-black/45 backdrop-blur-md">
          <div className="absolute inset-0 bg-[linear-gradient(135deg,rgba(255,255,255,0.1),transparent_42%,rgba(245,184,91,0.14))]" />
          <div className="relative flex h-12 items-center justify-between border-b border-white/12 px-4">
            <div className="flex gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-[#f5b85b]" />
              <span className="h-2.5 w-2.5 rounded-full bg-[#5b8fd6]" />
              <span className="h-2.5 w-2.5 rounded-full bg-[#2dd4bf]" />
            </div>
            <div className="rounded-md border border-white/14 bg-black/20 px-3 py-1 font-mono text-xs text-white/62">
              天玄·千枢科学发现平台.local
            </div>
          </div>
          <div className="relative grid h-[456px] grid-cols-[180px_minmax(0,1fr)_232px]">
            <div className="border-r border-white/10 bg-black/18 p-4">
              <div className="mb-5 h-3 w-28 rounded bg-white/30" />
              <div className="space-y-2">
                {["项目文件", "能力", "计算", "审批"].map(
                  (item, index) => (
                    <div
                      key={item}
                      className={[
                        "rounded-md border px-3 py-2 text-xs",
                        index === 1
                          ? "border-[#f5b85b]/55 bg-[#f5b85b]/16 text-[#ffe2ad]"
                          : "border-white/10 bg-white/[0.05] text-white/50",
                      ].join(" ")}
                    >
                      {item}
                    </div>
                  )
                )}
              </div>
              <div className="mt-8 space-y-2">
                <div className="ocs-flow-line h-1.5 rounded-full bg-[linear-gradient(90deg,#f5b85b,#5b8fd6,#2dd4bf,#5b8fd6,#f5b85b)] bg-[length:220%_100%]" />
                <div className="h-1.5 w-2/3 rounded-full bg-white/18" />
                <div className="h-1.5 w-4/5 rounded-full bg-white/14" />
              </div>
            </div>

            <div className="relative p-5">
              <div className="mb-5 flex items-center justify-between">
                <div>
                  <div className="mb-2 h-3 w-48 rounded bg-white/34" />
                  <div className="h-2 w-28 rounded bg-white/18" />
                </div>
                <div className="rounded-md border border-[#f5b85b]/45 bg-[#f5b85b]/16 px-3 py-1.5 text-xs font-semibold uppercase tracking-normal text-[#ffe2ad]">
                  运行中
                </div>
              </div>

              <div className="space-y-4">
                <div className="ocs-pulse-panel max-w-md rounded-lg border border-white/14 bg-white/[0.07] p-4">
                  <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-white/78">
                    <Sparkles className="h-4 w-4 text-[#f5b85b]" />
                    已生成计划
                  </div>
                  <div className="space-y-2">
                    <div className="h-2 rounded bg-white/28" />
                    <div className="h-2 w-5/6 rounded bg-white/18" />
                    <div className="h-2 w-2/3 rounded bg-white/14" />
                  </div>
                </div>

                <div className="ml-auto max-w-xs rounded-lg bg-[#f6f3fb] p-4 text-[#1e1233] shadow-xl shadow-black/25">
                  <div className="mb-3 flex items-center gap-2">
                    <Rocket className="h-4 w-4 text-[#1e5cb3]" />
                    <span className="text-sm font-semibold">产物就绪</span>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <div className="aspect-square rounded-md bg-[#a8c4ef]" />
                    <div className="aspect-square rounded-md bg-[#f5b85b]" />
                    <div className="aspect-square rounded-md bg-[#2dd4bf]" />
                  </div>
                </div>

                <div className="max-w-md rounded-lg border border-[#5b8fd6]/38 bg-[#5b8fd6]/14 p-4">
                  <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-[#a8c4ef]">
                    <ShieldCheck className="h-4 w-4" />
                    工具审批
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="h-9 w-28 rounded-md bg-[#f5b85b] text-center text-sm font-bold leading-9 text-[#1e1233]">
                      批准
                    </div>
                    <div className="h-2 flex-1 rounded-full bg-white/18">
                      <div className="h-full w-2/3 rounded-full bg-[linear-gradient(90deg,#f5b85b,#5b8fd6)]" />
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="border-l border-white/10 bg-black/14 p-4">
              <div className="mb-4 flex items-center gap-2 text-xs font-semibold uppercase tracking-normal text-[#a8c4ef]">
                <FileStack className="h-4 w-4" />
                预览
              </div>
              <div className="relative aspect-square overflow-hidden rounded-lg border border-white/14 bg-[linear-gradient(135deg,rgba(91,143,214,0.34),rgba(45,212,191,0.14),rgba(245,184,91,0.12))]">
                <div className="absolute inset-6 border border-white/20" />
                <div className="absolute left-8 top-8 h-16 w-16 rounded-md border border-white/25 bg-white/12" />
                <div className="absolute bottom-7 right-7 h-20 w-20 rounded-md border border-[#f5b85b]/40 bg-[#f5b85b]/18" />
              </div>
              <div className="mt-4 space-y-2">
                <div className="h-2 rounded bg-white/24" />
                <div className="h-2 w-2/3 rounded bg-white/16" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ShowcasePage() {
  return (
    <main className="min-h-screen bg-[#f6f3fb] text-[#1e1233]">
      <style>{`
        @keyframes ocsSweep {
          0% { transform: translateX(-34%) rotate(-10deg); opacity: 0.18; }
          45% { opacity: 0.9; }
          100% { transform: translateX(34%) rotate(-10deg); opacity: 0.3; }
        }
        @keyframes ocsFloat {
          0%, 100% { transform: translateY(0) rotate(-3deg); }
          50% { transform: translateY(-24px) rotate(-1.4deg); }
        }
        @keyframes ocsPanelPulse {
          0%, 100% { box-shadow: 0 0 0 rgba(245,184,91,0); }
          50% { box-shadow: 0 0 42px rgba(245,184,91,0.16); }
        }
        @keyframes ocsFlow {
          0% { background-position: 0% 50%; }
          100% { background-position: 220% 50%; }
        }
        .ocs-energy { animation: ocsSweep 5.8s ease-in-out infinite alternate; }
        .ocs-energy-delay { animation-delay: -2.2s; }
        .ocs-stage { animation: ocsFloat 5.4s ease-in-out infinite; }
        .ocs-pulse-panel { animation: ocsPanelPulse 3.8s ease-in-out infinite; }
        .ocs-flow-line { animation: ocsFlow 2.4s linear infinite; }
        .ocs-media::after {
          content: "";
          position: absolute;
          inset: 0;
          border: 1px solid color-mix(in srgb, var(--slot-glow), white 20%);
          border-radius: 8px;
          opacity: 0;
          transition: opacity 180ms ease;
          pointer-events: none;
        }
        .ocs-media:hover::after { opacity: 1; }
        @media (prefers-reduced-motion: reduce) {
          .ocs-energy,
          .ocs-stage,
          .ocs-pulse-panel,
          .ocs-flow-line {
            animation: none;
          }
        }
      `}</style>

      <section className="relative min-h-[760px] overflow-hidden bg-[#0a1830] text-white lg:min-h-[88vh]">
        <HeroScene />
        <div className="relative mx-auto min-h-[760px] max-w-7xl px-6 py-6 sm:px-8 lg:min-h-[88vh] lg:px-10">
          <div className="max-w-5xl pb-16 pt-24 sm:pt-28 lg:pt-32">
            <div className="mb-6 inline-flex items-center gap-2 text-base font-black tracking-normal text-[#f5b85b] sm:text-lg">
              <Sparkles className="h-5 w-5 text-[#f5b85b]" />
              中国航天科技体系与创新研究院智能体系统中心研发
            </div>
            <h1 className="max-w-full text-5xl font-black leading-none tracking-normal !text-white sm:text-7xl lg:text-[7rem] xl:text-[8rem]">
              天玄·千枢科学发现平台
            </h1>
            <p className="mt-5 max-w-3xl text-3xl font-black leading-tight text-white sm:text-5xl">
              把科研项目变成会行动的智能体工作台。
            </p>
            <p className="mt-6 max-w-2xl text-xl leading-8 text-white/82">
              把 Claude Science 式科研体验带给开源社区。
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-3">
              <a
                href={githubUrl}
                className="inline-flex h-14 items-center gap-2 rounded-md bg-[#f5b85b] px-6 text-base font-black text-[#0a1830] shadow-2xl shadow-[#f5b85b]/30 transition hover:-translate-y-0.5 hover:bg-[#ffd48a]"
              >
                <Star className="h-5 w-5" />
                在 GitHub 上 Star
              </a>
              <a
                href={macDownloadUrl}
                className="inline-flex h-14 items-center gap-2 rounded-md border border-white/22 bg-white px-6 text-base font-black text-[#0a1830] shadow-2xl shadow-white/12 transition hover:-translate-y-0.5 hover:bg-[#f6f3fb]"
              >
                <Download className="h-5 w-5" />
                下载 Mac 安装包
              </a>
            </div>
          </div>
        </div>
      </section>

      <section className="bg-[#f6f3fb] px-6 py-14 sm:px-8 lg:px-10">
        <div className="mx-auto max-w-7xl">
          <div className="mb-8 flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
            <div>
              <p className="text-sm font-black uppercase tracking-normal text-[#1e5cb3]">
                功能概览
              </p>
              <h2 className="mt-3 max-w-3xl text-4xl font-black leading-tight tracking-normal">
                不止是聊天入口，而是科研项目工作台。
              </h2>
              <p className="mt-4 max-w-3xl text-sm leading-6 text-[#706b78]">
                天玄·千枢科学发现平台 基于 DeepAgents 和 LangGraph，围绕科研项目重组
                runtime、workspace、skills、tools 和授权流程。
              </p>
            </div>
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {projectHighlights.map((highlight) => {
              const Icon = highlight.icon;
              return (
                <article
                  key={highlight.title}
                  className="group relative overflow-hidden rounded-lg border border-[#d1dae7] bg-white p-5 shadow-sm transition duration-300 hover:-translate-y-1 hover:border-[#7ba3e0] hover:shadow-xl hover:shadow-[#1e5cb3]/12"
                >
                  <div className="absolute inset-x-0 top-0 h-1 bg-[linear-gradient(90deg,#f5b85b,#5b8fd6,#2dd4bf)] opacity-0 transition group-hover:opacity-100" />
                  <Icon className="h-6 w-6 text-[#1e5cb3]" />
                  <h3 className="mt-5 text-xl font-black tracking-normal">
                    {highlight.title}
                  </h3>
                  <p className="mt-2 text-sm leading-6 text-[#706b78]">
                    {highlight.copy}
                  </p>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      <section className="border-y border-[#d1dae7] bg-[#dce8f7] px-6 py-16 sm:px-8 lg:px-10">
        <div className="mx-auto max-w-7xl">
          <div className="mb-8 flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
            <div>
              <p className="text-sm font-black uppercase tracking-normal text-[#1e5cb3]">
                工作台界面
              </p>
              <h2 className="mt-3 text-[36px] font-black leading-tight tracking-normal lg:whitespace-nowrap">
                三栏工作台，把对话、文件和运行状态放在一起。
              </h2>
              <p className="mt-4 max-w-2xl text-sm leading-6 text-[#706b78]">
                右侧面板保持项目文件和产物可见，中间区域专注当前任务，左侧管理项目、会话、设置和技能。
              </p>
            </div>
          </div>
          <div className="overflow-hidden rounded-lg border border-[#d1dae7] bg-white shadow-2xl shadow-[#1e5cb3]/12">
            <div
              className="aspect-[16/8] bg-cover bg-left-top"
              style={{
                backgroundImage:
                  "linear-gradient(180deg, rgba(246,243,251,0.08), rgba(246,243,251,0.1)), url('/showcase/workspace-preview-cn.jpeg')",
              }}
            />
          </div>
        </div>
      </section>

      <section
        id="demo"
        className="bg-[#12091f] px-6 py-16 text-white sm:px-8 lg:px-10"
      >
        <div className="mx-auto max-w-7xl">
          <div className="mb-8 flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
            <div>
              <p className="text-sm font-black uppercase tracking-normal text-[#f5b85b]">
                例子演示
              </p>
              <h2 className="mt-3 text-[36px] font-black leading-tight tracking-normal !text-white">
                三个科学场景，展示从问题到产出。
              </h2>
              <p className="mt-4 max-w-2xl text-sm leading-6 text-white/68">
                提问、检查、审批、运行、预览，过程不断线。
              </p>
            </div>
          </div>
          <WorkflowSwitcher />
        </div>
      </section>

      <section className="bg-[#f6f3fb] px-6 py-16 sm:px-8 lg:px-10">
        <div className="mx-auto max-w-7xl">
          <div className="mb-8 flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
            <div>
              <p className="text-sm font-black uppercase tracking-normal text-[#1e5cb3]">
                功能实景
              </p>
              <h2 className="mt-3 text-[36px] font-black leading-tight tracking-normal">
                科研需要的工具，都在 天玄·千枢科学发现平台 里。
              </h2>
              <p className="mt-4 max-w-2xl text-sm leading-6 text-[#706b78]">
                看数据、连工具、调计算、管权限、复用技能，都放在同一个工作台里。
              </p>
            </div>
          </div>
          <CapabilityStack />
        </div>
      </section>

      <section className="relative overflow-hidden bg-[#0a1830] px-6 py-16 text-white sm:px-8 lg:px-10">
        <div className="absolute inset-0 bg-[linear-gradient(115deg,#0a1830,#122448_55%,#123a8b)]" />
        <div className="absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,#f5b85b,#5b8fd6,transparent)]" />
        <div
          aria-hidden="true"
          className="absolute -right-20 top-8 hidden h-36 w-[520px] bg-contain bg-right bg-no-repeat opacity-20 lg:block"
          style={{ backgroundImage: "url('/showcase/internagentS.png')" }}
        />
        <div className="relative mx-auto flex max-w-7xl flex-col justify-between gap-8 lg:flex-row lg:items-center">
          <div>
            <p className="text-sm font-black uppercase tracking-normal text-[#f5b85b]">
              中国航天科技体系与创新研究院智能体系统中心研发
            </p>
            <h2 className="mt-3 max-w-3xl text-4xl font-black leading-tight tracking-normal !text-white sm:text-5xl">
              天玄·千枢科学发现平台：面向开源社区的科研智能体工作台。
            </h2>
          </div>
          <a
            href={githubUrl}
            className="inline-flex h-16 w-fit items-center gap-3 rounded-md bg-[#f5b85b] px-7 text-lg font-black text-[#0a1830] shadow-2xl shadow-[#f5b85b]/30 transition hover:-translate-y-0.5 hover:bg-[#ffd48a]"
          >
            <Github className="h-5 w-5" />
            在 GitHub 上 Star
            <ArrowRight className="h-5 w-5" />
          </a>
        </div>
      </section>
    </main>
  );
}
