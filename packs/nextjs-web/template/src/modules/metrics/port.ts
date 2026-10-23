// The metrics port: count each request a route handler answers, and expose the counts for
// scraping at /api/metrics.
export interface Scrape {
  body: string;
  contentType: string;
}

export interface Metrics {
  count(route: string, status: number): void;
  scrape(): Promise<Scrape>;
}
