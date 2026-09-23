import { afterEach, describe, expect, it, vi } from 'vitest';
import { streamAgentFormSubmit, submitAgentForm } from './agent';

/**
 * A pending form's `instance_id` (register id GAP-136) has to reach the agent service on
 * submit, or a stale browser tab can answer a form the agent has since replaced. These pin
 * the one point a form submit puts it: the request body.
 */
describe('form submit carries the form instance id', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('submitAgentForm sends form_instance_id when the pending form has one', async () => {
    const fetchMock = vi
      .spyOn(global, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ session_id: 's1', status: 'idle' }), { status: 200 }));

    await submitAgentForm('s1', { plan_confirmed: true }, 'form-instance-7');

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      values: { plan_confirmed: true },
      form_instance_id: 'form-instance-7',
    });
  });

  it('submitAgentForm omits form_instance_id for an older form with none', async () => {
    const fetchMock = vi
      .spyOn(global, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ session_id: 's1', status: 'idle' }), { status: 200 }));

    await submitAgentForm('s1', { plan_confirmed: true });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ values: { plan_confirmed: true } });
  });

  it('submitAgentForm surfaces the server detail when the form was replaced', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'stale_form' }), { status: 409 }),
    );

    await expect(submitAgentForm('s1', {}, 'form-instance-7')).rejects.toThrow('stale_form');
  });

  function sseResponse(status = 200) {
    return new Response(
      new ReadableStream({
        start(controller) {
          controller.close();
        },
      }),
      { status },
    );
  }

  it('streamAgentFormSubmit sends form_instance_id when the pending form has one', async () => {
    const fetchMock = vi.spyOn(global, 'fetch').mockImplementation(async (url) => {
      if (String(url).endsWith('/form-submit-stream')) return sseResponse();
      return new Response(JSON.stringify({ session_id: 's1', status: 'idle' }), { status: 200 });
    });

    await streamAgentFormSubmit('s1', { plan_confirmed: true }, 'form-instance-7', () => {});

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      values: { plan_confirmed: true },
      form_instance_id: 'form-instance-7',
    });
  });

  it('streamAgentFormSubmit omits form_instance_id for an older form with none', async () => {
    const fetchMock = vi.spyOn(global, 'fetch').mockImplementation(async (url) => {
      if (String(url).endsWith('/form-submit-stream')) return sseResponse();
      return new Response(JSON.stringify({ session_id: 's1', status: 'idle' }), { status: 200 });
    });

    await streamAgentFormSubmit('s1', { plan_confirmed: true }, undefined, () => {});

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ values: { plan_confirmed: true } });
  });

  it('streamAgentFormSubmit surfaces the server detail when the form was replaced', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'stale_form' }), { status: 409 }),
    );

    await expect(
      streamAgentFormSubmit('s1', {}, 'form-instance-7', () => {}),
    ).rejects.toThrow('stale_form');
  });
});
