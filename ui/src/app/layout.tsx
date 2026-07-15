import { NuqsAdapter } from "nuqs/adapters/next/app";
import Script from "next/script";
import { Toaster } from "sonner";
import { AppFooter } from "@/app/components/AppFooter";
import "katex/dist/katex.min.css";
import "./globals.css";

const themeBootstrapScript = `
(() => {
  try {
    const theme = localStorage.getItem("internagents.theme") === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = theme;
    document.documentElement.classList.toggle("dark", theme === "dark");
    document.documentElement.setAttribute("data-joy-color-scheme", theme);
  } catch {
    document.documentElement.dataset.theme = "light";
  }
})();
`;

const startupSplashDismissScript = `
(() => {
  const markReady = () => {
    document.body.dataset.internagentsStartup = "ready";
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      requestAnimationFrame(markReady);
    }, { once: true });
  } else {
    requestAnimationFrame(markReady);
  }
})();
`;

const startupSplashLanguageScript = `
(() => {
  try {
    const storedLanguage = localStorage.getItem("internagents.ui.language");
    const inferredLanguage = navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
    const language = storedLanguage === "zh" || storedLanguage === "en" ? storedLanguage : inferredLanguage;
    const text = language === "en" ? "Starting 天玄·千枢科学发现平台..." : "天玄·千枢科学发现平台 \\u6b63\\u5728\\u542f\\u52a8\\u4e2d...";
    document.documentElement.lang = language === "en" ? "en" : "zh-CN";
    const target = document.querySelector("[data-internagents-startup-text]");
    if (target) {
      target.textContent = text;
    }
  } catch {
  }
})();
`;

export const metadata = {
  title: "天玄·千枢科学发现平台",
  description: "Local 天玄·千枢科学发现平台 UI",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
    >
      <body
        className="min-h-screen bg-background text-foreground"
        suppressHydrationWarning
      >
        <Script
          id="internagents-theme-bootstrap"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: themeBootstrapScript }}
        />
        <div
          id="internagents-startup-splash"
          className="internagents-startup-splash"
          role="status"
          aria-live="polite"
        >
          <span className="internagents-startup-splash__spinner" />
          <span
            data-internagents-startup-text
            suppressHydrationWarning
          >
            天玄·千枢科学发现平台
          </span>
        </div>
        <Script
          id="internagents-startup-language"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: startupSplashLanguageScript }}
        />
        <Script
          id="internagents-desktop-config"
          src="/api/runtime/desktop-config"
          strategy="beforeInteractive"
        />
        <NuqsAdapter>
          <div className="min-h-[calc(100vh-var(--app-footer-height))]">
            {children}
          </div>
        </NuqsAdapter>
        <AppFooter />
        <Toaster />
        <Script
          id="internagents-startup-dismiss"
          strategy="afterInteractive"
          dangerouslySetInnerHTML={{ __html: startupSplashDismissScript }}
        />
      </body>
    </html>
  );
}
