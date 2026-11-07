import { Link } from 'react-router'

export default function NotFoundPage() {
  return <section>
    <h1>Page not found</h1>
    <p>That page does not exist.</p>
    <Link className="button-link" to="/capture">Go to Capture</Link>
  </section>
}
