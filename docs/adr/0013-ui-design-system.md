# ADR-0013: UI design system: Tailwind v4 + shadcn-style components on Radix

**Status:** accepted · 2026-09-29

## Context
The UI grew page by page on one hand-written stylesheet. Every new screen
re-invented buttons, badges and layout. There was no dark mode, no keyboard
navigation across pages, and it didn't work well on phones.

## Decision
- **Tailwind v4** (`@tailwindcss/vite`), with semantic colour tokens (`--background`,
  `--primary`, `--ok`/`--warn`/`--bad`/`--info`, …) defined once in
  `web/src/styles.css` for light and dark.
- **Components we own**, copied in shadcn style into `web/src/components/ui/`:
  Button, Card, Badge, DropdownMenu, Tooltip. They are built on Radix primitives
  for accessibility, with no runtime component library.
- **App shell:** sidebar navigation, a ⌘K command palette (cmdk), a user menu with
  theme (dark default, light, system), toasts (sonner) and lucide icons.
- **Compatibility layer:** the existing class names (`.card`, `.pill`, `.row`,
  the lane builder classes, …) are redefined with `@apply`, so pages can move to
  the components gradually rather than in one big rewrite.
- Vendor chunks are split (react, ui, dnd), so app changes don't bust the
  framework cache.

## Consequences
- New pages use the `ui/` components and utilities. The compatibility classes are
  only for existing markup.
- Colours come only from the tokens, so a company theme is a CSS-variable change.
- Pages must fit a 390px-wide screen: grids use `minmax(0,1fr)`, and tables
  scroll inside their card on phones.
