/** "manage-phone-light.png" → "manage · phone · light": which screen a route screenshot (and a UI
 * defect found on it) shows. The e2e suite names them route-size-scheme.png. */
export function screenName(file: string): string {
  return file
    .replace(/\.png$/, "")
    .replace(/-(desktop|phone)-(light|dark)$/, " · $1 · $2");
}
