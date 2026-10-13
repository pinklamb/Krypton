const channel = db.client().subscribe();
channel.on('message', onMessage);
channel.on('error', onError);
channel.on('close', onClose);
