import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'AppUAT — Map your app, plan UAT',
  description: 'Generic UAT planning tool for product managers',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b border-zinc-800 px-6 py-4">
          <div className="max-w-6xl mx-auto flex items-center justify-between">
            <a href="/" className="flex items-center gap-2">
              <span className="text-2xl">🧭</span>
              <span className="font-semibold text-lg">AppUAT</span>
              <span className="text-xs text-zinc-500 ml-1">v0.1</span>
            </a>
            <span className="text-sm text-zinc-500">Map any app · Plan any UAT</span>
          </div>
        </header>
        <main className="max-w-6xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  )
}
