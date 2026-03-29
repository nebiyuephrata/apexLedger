export type AuthHeadersResolver = () => Promise<Record<string, string>>;

let resolver: AuthHeadersResolver = async () => ({});

export const registerAuthHeadersResolver = (nextResolver: AuthHeadersResolver) => {
  resolver = nextResolver;
};

export const getAuthHeaders = async (): Promise<Record<string, string>> => resolver();
