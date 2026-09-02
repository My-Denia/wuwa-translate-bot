import { handleAcceptance, acceptancePage } from '../../lib/owner-acceptance.js';
const worker = {
  fetch(request, env) {
    return request.method === 'GET' ? acceptancePage(env) : handleAcceptance(request, env);
  },
};
export default worker;
