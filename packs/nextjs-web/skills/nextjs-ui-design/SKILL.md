---
name: nextjs-ui-design
description: How to design the pages of a nextjs-web product with the template's design system (Tailwind v4 tokens, shadcn/ui components, the app shell) and write docs/ui.md. Preloaded for the designer in the design station; the developer follows docs/ui.md.
---

# Designing a nextjs-web product's UI

You are the **designer**. The architect has written the spec, design, decisions and tasks. Your job is
`docs/ui.md`: what every page looks like and how it behaves, using only what the template provides, so
the developer can build it without guessing and the reviewer can check it against the screenshots.

## What the template gives you (use it, don't reinvent it)

- **Look**: `src/app/look.ts` → `LOOK` (from the `look` answer in `docs/decisions.yaml`: `neutral`,
  `neutral-compact`, `warm`, `bold`), `APP_NAME` (the product's name) and `NAV` (the main navigation).
  The token sets live in `src/app/globals.css`; every look has light and dark modes.
- **Tokens through Tailwind only**: `bg-background`, `text-foreground`, `bg-card`, `text-muted-foreground`,
  `border`, `bg-primary text-primary-foreground`, `text-destructive`, `text-success`, `rounded-md`.
  Never raw colours (`#…`, `text-blue-600`) or new fonts (builds have no network).
- **Frame**: `src/components/app-shell.tsx` (header, navigation, account menu, skip link; in the root
  layout already) and `src/components/page.tsx` → `<Page title description actions>` for every page: the
  one `<main>` and the one `<h1>`.
- **Components** in `src/components/ui/`: `Button` (variants default, outline, secondary, ghost,
  destructive, link; `asChild` for links), `Input`, `Label`, `Textarea`, `Select` (native), `Card`
  (+ Header, Title, Description, Content, Footer), `Table` (+ Header, Body, Row, Head, Cell; scrolls inside
  its own box on a phone), `Badge` (default, secondary, outline, success, destructive), `Dialog`
  (client component: confirmations), `toast` / `Toaster` (sonner: "Saved", "Deleted"), `EmptyState`.
  Add another shadcn/ui component only by copying its source into `src/components/ui/` and say why in
  `docs/ui.md`.

## Write docs/ui.md

One section per page (`## /books` …), in the order a person meets them. For each:

1. **Purpose** — one sentence, and the requirement ids it serves (R1, …).
2. **Who** — public, signed in, or admin (match `docs/decisions.yaml` and the authz design).
3. **Layout** — the `<Page>` title, description and actions, then the blocks top to bottom (cards,
   table columns, form fields with their labels and validation). Desktop first, then what changes at
   390 px (stacked columns, a table that scrolls, actions under the title).
4. **Components** — which ones from the list above, with variants (the main action is the default
   button; one per page).
5. **States** — every one that can happen: **empty** (what goes here and how to add the first one:
   `EmptyState` with an action), **loading** (server-rendered pages need none; say so), **error**
   (inline next to the field; a failed save keeps the input), **signed out** (redirect to `/sign-in`
   with `next`) and **forbidden** (what a member sees on an admin page).
6. **Interactions** — what each action does, its confirmation (a `Dialog` for anything destructive) and
   its feedback (a toast, or the list updating).

End with **Navigation**: the `NAV` entries (label → path) for `src/app/look.ts`, and every route for
`e2e/routes.ts` (`ROUTES`; and `SIGNED_IN_ROUTES` for those that need a signed-in person).

## Rules

- Design for the requirements only; no pages, filters or settings nobody asked for.
- Accessible by construction: labels on every control, one `<h1>`, buttons for actions and links for
  navigation, focus visible, colour never the only signal (a badge has text).
- Words a person reads are plain and specific ("Add book", "No books on loan yet"), never "Submit" or
  "No data".
- Don't write code or change other files: the developer builds from `docs/ui.md`.
