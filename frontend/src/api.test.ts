import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, request, setCsrfToken } from './api';
afterEach(()=>{vi.unstubAllGlobals();setCsrfToken();});
describe('API security boundary',()=>{
 it('sends CSRF on mutations and keeps credentials same-origin',async()=>{const fetch=vi.fn().mockResolvedValue(new Response(JSON.stringify({id:'n'})));vi.stubGlobal('fetch',fetch);setCsrfToken('csrf-test');await api.create('workspace/id','Notebook');expect(fetch).toHaveBeenCalledWith('/api/workspaces/workspace%2Fid/notebooks',expect.objectContaining({credentials:'same-origin',method:'POST',headers:expect.objectContaining({'X-CSRF-Token':'csrf-test'}),body:'{"title":"Notebook"}'}));});
 it('never displays arbitrary error body or leaked upstream secret',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('secret-do-not-display',{status:503})));await expect(request('/auth/session')).rejects.toThrow('noch nicht eingerichtet');});
 it('handles empty successful deletes',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(null,{status:204})));await expect(api.remove('notebook')).resolves.toBeUndefined();});
 it('does not send CSRF on reads',async()=>{const fetch=vi.fn().mockResolvedValue(new Response('{"user":null}'));vi.stubGlobal('fetch',fetch);setCsrfToken('csrf-test');await api.session();expect(fetch.mock.calls[0][1].headers).not.toHaveProperty('X-CSRF-Token');});
});
