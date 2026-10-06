db.client().subscribe().on('message', onMessage).on('error', onError).on('close', onClose);
