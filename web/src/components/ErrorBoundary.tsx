import { Component, type ReactNode } from "react";

/** Keeps a crash in one page from blanking the whole app; shows the error instead. */
export default class ErrorBoundary extends Component<
  { children: ReactNode; resetKey?: string },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error)
      this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <section className="card">
        <h3>This page hit an error</h3>
        <pre className="pre error">
          {String(this.state.error?.stack ?? this.state.error)}
        </pre>
        <button onClick={() => this.setState({ error: null })}>
          Try again
        </button>
      </section>
    );
  }
}
