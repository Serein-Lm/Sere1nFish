const DEFAULT_RETURN_PATH = '/dashboard'

function hasControlCharacters(value: string): boolean {
  return Array.from(value).some(character => {
    const code = character.charCodeAt(0)
    return code <= 31 || code === 127
  })
}

export function safeReturnPath(value: unknown): string | null {
  if (
    typeof value !== 'string'
    || value.length > 4096
    || !value.startsWith('/')
    || value.startsWith('//')
    || value.includes('\\')
    || hasControlCharacters(value)
  ) {
    return null
  }
  return value.startsWith('/login') ? null : value
}

export function currentReturnPath(): string {
  return safeReturnPath(
    `${window.location.pathname}${window.location.search}${window.location.hash}`,
  ) || DEFAULT_RETURN_PATH
}

export function buildLoginPath(returnPath: unknown = currentReturnPath()): string {
  const params = new URLSearchParams()
  params.set('return_to', safeReturnPath(returnPath) || DEFAULT_RETURN_PATH)
  return `/login?${params.toString()}`
}

export function resolveLoginReturnPath(state: unknown, search: string): string {
  const statePath = (state as { from?: unknown } | null)?.from
  const queryPath = new URLSearchParams(search).get('return_to')
  return safeReturnPath(statePath) || safeReturnPath(queryPath) || DEFAULT_RETURN_PATH
}

export function redirectToLogin(): void {
  if (window.location.pathname !== '/login') {
    window.location.assign(buildLoginPath())
  }
}
