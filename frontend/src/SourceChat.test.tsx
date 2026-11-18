import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { api, request, type SourceDocument } from './api';
import { SourceChat } from './SourceChat';
vi.mock('./api', () => ({ api: { connections: vi.fn(), sourceText: vi.fn() }, request: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const source: SourceDocument = { notebook_id:'n', id:'d', version_id:'v', filename:'Probe.txt', status:'ready', error_code:null };
it.each([true,false])('reads current authorized text when opening the citation: accessible=%s', async accessible => {
  vi.mocked(api.connections).mockResolvedValue({items:[{id:'p', label:'Meine KI', model:'m', status:'active', provider_id:'endpoint',last_tested_at:null}]});
  vi.mocked(request).mockResolvedValue({answer:'Blau.',citation:{notebook_id:'n',document_id:'d',version_id:'v',chunk_id:'c',ordinal:3,quote:'blau'}});
  if(accessible) vi.mocked(api.sourceText).mockResolvedValue({document:source,items:[{id:'c',ordinal:3,text:'Die Probe ist blau.',page:null,truncated:false}],next_ordinal:null});
  else vi.mocked(api.sourceText).mockRejectedValue(new Error('private upstream details'));
  render(<SourceChat source={source}/>); await screen.findByRole('option',{name:/Meine KI/});
  await userEvent.selectOptions(screen.getByRole('combobox'),'p'); await userEvent.type(screen.getByRole('textbox'),'Welche Farbe?');
  await userEvent.click(screen.getByRole('button',{name:'Frage senden'})); await userEvent.click(await screen.findByRole('button',{name:/Beleg in Probe/}));
  expect(api.sourceText).toHaveBeenCalledWith('n','d','v',3);
  if(accessible) expect(await screen.findByText('Die Probe ist blau.')).toBeVisible();
  else { expect(await screen.findByRole('alert')).toHaveTextContent('nicht mehr verfügbar'); expect(screen.queryByText('Blau.')).not.toBeInTheDocument(); }
});
