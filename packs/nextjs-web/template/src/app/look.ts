// The product's look (ADR-0044 §4): the `look` answer in docs/decisions.yaml. It picks the token
// set in src/app/globals.css (<html data-look>); every theme has light and dark modes and the same
// accessible components. Change it here when the decision changes, nowhere else.
export const LOOKS = ["neutral", "neutral-compact", "warm", "bold"] as const;
export type Look = (typeof LOOKS)[number];

export const LOOK: Look = "neutral";

/** The product's name, shown in the header and the browser tab. */
export const APP_NAME = "App";

/** The app's main navigation: add a page here when you add one a person opens from the menu. */
export const NAV: { href: string; label: string }[] = [{ href: "/", label: "Home" }];
