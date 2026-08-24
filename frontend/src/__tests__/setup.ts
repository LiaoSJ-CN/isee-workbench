import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// Auto-cleanup RTL renders between tests so DOM state doesn't leak.
afterEach(() => {
  cleanup();
});
