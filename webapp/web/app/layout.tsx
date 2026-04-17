import type { Metadata } from 'next'
import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'
import './globals.css'

export const metadata: Metadata = {
  title: 'Prism — See your product from every angle',
  description: 'Test your app, track competitors, research your industry — one platform for product teams',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans">
        <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur-sm px-6 py-4">
          <div className="max-w-6xl mx-auto flex items-center justify-between">
            <a href="/" className="flex items-center gap-2">
              <svg width="22" height="22" viewBox="0 0 256 256" fill="none" xmlns="http://www.w3.org/2000/svg">
                <circle cx="128" cy="128" r="120" stroke="#10b981" strokeWidth="16" fill="none" />
                <circle cx="128" cy="128" r="8" fill="#10b981" />
                <line x1="128" y1="128" x2="128" y2="48" stroke="#10b981" strokeWidth="12" strokeLinecap="round" />
                <line x1="128" y1="128" x2="196" y2="168" stroke="#10b981" strokeWidth="12" strokeLinecap="round" />
                <circle cx="128" cy="28" r="8" fill="#10b981" />
                <circle cx="215" cy="78" r="8" fill="#10b981" />
                <circle cx="215" cy="178" r="8" fill="#10b981" />
                <circle cx="128" cy="228" r="8" fill="#10b981" />
                <circle cx="41" cy="178" r="8" fill="#10b981" />
                <circle cx="41" cy="78" r="8" fill="#10b981" />
              </svg>
              <span className="text-lg font-semibold tracking-tight">Prism</span>
              <span className="text-[10px] text-zinc-600 bg-zinc-800 px-1.5 py-0.5 rounded">beta</span>
            </a>
            <span className="text-xs text-zinc-500">Product intelligence platform</span>
          </div>
        </header>
        <main className="max-w-6xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  )
}
